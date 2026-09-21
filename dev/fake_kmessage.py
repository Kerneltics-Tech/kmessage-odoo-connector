#!/usr/bin/env python3
"""A stand-in for the K-Message API, for tests and local demos.

It answers the handful of routes the connector calls, in the same envelope the
real service uses (``{"status": …, "message": …, "data": …}``), and remembers
what it was sent so a test can assert on it:

    GET  /api/me                      who the key belongs to
    GET  /api/accounts                the tenant's WhatsApp numbers
    GET  /api/templates               approved templates
    GET  /api/webhooks                subscriptions + the event catalogue
    POST /api/webhooks                create one (returns the secret once)
    DELETE /api/webhooks/{id}
    POST /api/integrations/odoo/connect   wire a whole Odoo up in one call
    POST /api/templates               write a template (DRAFT)
    PUT  /api/templates/{id}          change one, e.g. to attach a media handle
    POST /api/templates/{id}/publish  submit it to Meta
    POST /api/templates/upload-media  hand Meta the sample a media header needs
    POST /api/messages/template       send a template (multipart or JSON)
    POST /api/contacts/{id}/messages  send free-form text
    GET  /api/contacts                look a contact up by phone

    GET  /_sent                       everything received so far (test hook)
    POST /_reset                      forget it again (test hook)
    POST /_fail                       make the next N sends fail (test hook)
    POST /_refuse_tools               answer 403 to tool publishing, like a managed plan
    POST /_approve                    Meta gets back to us: {"name": …} → APPROVED

    GET  /api/chatbot/ai-contexts     the assistant tools a company has registered
    POST /api/chatbot/ai-contexts     register one

Run it with ``python3 dev/fake_kmessage.py --port 8787``; the key it accepts is
``test-key`` unless ``--api-key`` says otherwise. Nothing here talks to Meta,
and nothing leaves the process.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ACCOUNTS = [
    {
        "id": "acc-0000-0000-0000-000000000001",
        "name": "main number ",  # the trailing space is deliberate: the real API has one
        "phone_id": "100000000000001",
        "display_phone_number": "+966500000000",
        "is_default_outgoing": True,
        "is_default_incoming": True,
        "status": "active",
    }
]

TEMPLATES = [
    {
        "id": "tpl-0000-0000-0000-000000000001",
        "name": "invoice_ready",
        "language": "ar",
        "status": "APPROVED",
        "category": "UTILITY",
        "header_type": "DOCUMENT",
        "header_content": "",
        "body_content": "مرحباً {{1}}، فاتورتك رقم {{2}} بمبلغ {{3}} جاهزة.",
        "footer_content": "",
        "buttons": [{"type": "QUICK_REPLY", "text": "تم"}],
        "sample_values": [
            {"component": "body", "index": 1, "value": "عميل"},
            {"component": "body", "index": 2, "value": "INV/2026/0001"},
            {"component": "body", "index": 3, "value": "100.00"},
        ],
        "whatsapp_account": "main number ",
    },
    {
        "id": "tpl-0000-0000-0000-000000000002",
        "name": "order_confirmed",
        "language": "ar",
        "status": "APPROVED",
        "category": "UTILITY",
        "header_type": "",
        "body_content": "طلبك {{1}} تم تأكيده.",
        "buttons": [],
        "sample_values": [],
        "whatsapp_account": "main number ",
    },
]

EVENTS = [
    ("message.incoming", "Message Incoming", "When a new message is received from a contact"),
    ("message.sent", "Message Sent", "When an agent sends a message"),
    ("contact.created", "Contact Created", "When a new contact is created"),
    ("transfer.created", "Transfer Created", "When a transfer to human agent is requested"),
    ("transfer.assigned", "Transfer Assigned", "When a transfer is assigned to an agent"),
    ("transfer.resumed", "Transfer Resumed", "When chatbot is resumed (transfer closed)"),
    ("button.reply", "Button Reply", "When a contact taps a button"),
]


def _parse_multipart(body, content_type):
    """Split a multipart body into its plain fields and its one uploaded file.

    Written on email.parser rather than cgi, which was removed in Python 3.13 —
    this file has to run on whatever interpreter the reader happens to have.
    """
    parsed = message_from_bytes(
        b'Content-Type: ' + content_type.encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + body)
    fields, upload = {}, None
    for part in parsed.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        disposition = part.get('Content-Disposition') or ''
        name = _disposition_value(disposition, 'name')
        filename = _disposition_value(disposition, 'filename')
        payload = part.get_payload(decode=True) or b''
        if filename:
            upload = {'filename': filename, 'size': len(payload),
                      'content_type': part.get_content_type(),
                      'looks_like_pdf': payload[:5] == b'%PDF-'}
        elif name:
            fields[name] = payload.decode('utf-8', 'replace')
    return fields, upload


def _disposition_value(disposition, key):
    match = re.search(r'%s="([^"]*)"' % key, disposition)
    return match.group(1) if match else ''


class State:
    """Everything the fake remembers, guarded by one lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sent: list[dict] = []
        self.webhooks: dict[str, dict] = {}
        self.secrets: dict[str, str] = {}
        self.fail_next = 0
        self.fail_status = 503
        self.contexts: dict[str, dict] = {}
        self.drafts: dict[str, dict] = {}
        self.uploads: list[dict] = []
        self.refuse_tools = False

    def reset(self) -> None:
        with self.lock:
            self.sent.clear()
            self.webhooks.clear()
            self.secrets.clear()
            self.contexts.clear()
            self.drafts.clear()
            self.uploads.clear()
            self.fail_next = 0
            self.refuse_tools = False


STATE = State()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Handler(BaseHTTPRequestHandler):
    api_key = "test-key"
    protocol_version = "HTTP/1.1"

    def handle_one_request(self):
        """One request at a time, each with its own body.

        The handler instance is reused for every request on a keep-alive
        connection, so anything remembered per request has to be forgotten
        here — a cached body that outlives its request is served to the next
        one, which is a stranger bug to chase than no cache at all.
        """
        self._read_body = None
        super().handle_one_request()

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: A003 - silence the default stderr spam
        pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data) -> None:
        self._send(200, {"status": "success", "data": data})

    def _err(self, status: int, message: str) -> None:
        self._send(status, {"status": "error", "message": message, "data": None})

    def _authed(self) -> bool:
        if self.headers.get("X-API-Key") == self.api_key:
            return True
        self._err(401, "Missing authorization")
        return False

    def _body(self) -> bytes:
        """The request body, read exactly once and remembered.

        Reading it once is not an optimisation, it is the difference between
        working and not. These connections are keep-alive: a handler that
        answers without reading the body leaves those bytes in the socket, and
        the server then parses them as the *next* request line — which it
        answers with an HTML `501 Unsupported method` that has nothing to do
        with what the caller asked. Draining on every path, including the
        refusals, is what keeps one rejected request from corrupting the one
        after it.
        """
        if self._read_body is None:
            length = int(self.headers.get("Content-Length") or 0)
            self._read_body = self.rfile.read(length) if length else b""
        return self._read_body

    # -- routes -----------------------------------------------------------
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        path = self.path.split("?", 1)[0]
        if path in ("/_sent",):
            with STATE.lock:
                return self._ok({"sent": list(STATE.sent)})
        if not self._authed():
            return
        if path == "/api/me":
            return self._ok(
                {
                    "id": "usr-1",
                    "email": "connector@example.com",
                    "full_name": "Odoo Connector",
                    "organization_id": "org-1",
                    "organization": {"id": "org-1", "name": "Example Co"},
                }
            )
        if path == "/api/accounts":
            return self._ok({"accounts": ACCOUNTS})
        if path == "/api/templates":
            with STATE.lock:
                rows = TEMPLATES + list(STATE.drafts.values())
            return self._ok({"templates": rows, "total": len(rows), "page": 1, "limit": 50})
        if path == "/api/webhooks":
            with STATE.lock:
                hooks = list(STATE.webhooks.values())
            return self._ok(
                {
                    "webhooks": hooks,
                    "total": len(hooks),
                    "page": 1,
                    "limit": 50,
                    "available_events": [
                        {"value": v, "label": lbl, "description": desc} for v, lbl, desc in EVENTS
                    ],
                }
            )
        if path == "/api/contacts":
            return self._ok({"contacts": [], "total": 0, "page": 1, "limit": 50})
        if path == "/api/chatbot/ai-contexts":
            with STATE.lock:
                return self._ok({"contexts": list(STATE.contexts.values())})
        return self._err(404, "404 page not found")

    def do_POST(self):  # noqa: N802
        self._body()  # drained here so no early refusal can leave bytes behind
        path = self.path.split("?", 1)[0]
        if path == "/_reset":
            STATE.reset()
            return self._ok({"reset": True})
        if path == "/_refuse_tools":
            payload = json.loads(self._body() or b"{}")
            with STATE.lock:
                STATE.refuse_tools = bool(payload.get("refuse", True))
            return self._ok({"refuse_tools": STATE.refuse_tools})
        if path == "/_fail":
            payload = json.loads(self._body() or b"{}")
            with STATE.lock:
                STATE.fail_next = int(payload.get("count", 1))
                STATE.fail_status = int(payload.get("status", 503))
            return self._ok({"fail_next": payload.get("count", 1)})
        if path == "/_approve":
            # Meta getting back to us. Real approval takes minutes; a demo or a
            # test cannot wait for it, and a template that never becomes
            # sendable would make the stand-in lie about the interesting half.
            payload = json.loads(self._body() or b"{}")
            wanted = payload.get("name")
            with STATE.lock:
                changed = [
                    t["name"] for t in STATE.drafts.values()
                    if wanted in (None, t["name"])
                ]
                for record in STATE.drafts.values():
                    if wanted in (None, record["name"]):
                        record["status"] = "APPROVED"
            return self._ok({"approved": changed})
        if not self._authed():
            return

        if path == "/api/webhooks":
            payload = json.loads(self._body() or b"{}")
            url = (payload.get("url") or "").strip()
            if not url:
                return self._err(400, "url is required")
            if re.search(r"://(localhost|127\.|10\.|192\.168\.|169\.254\.)", url):
                return self._err(400, "callback_url must not be a private or loopback host")
            hook_id = str(uuid.uuid4())
            secret = payload.get("secret") or uuid.uuid4().hex
            record = {
                "id": hook_id,
                "name": payload.get("name") or "Webhook",
                "url": url,
                "events": payload.get("events") or [],
                "headers": payload.get("headers") or {},
                "is_active": payload.get("is_active", True),
                "has_secret": True,
                "created_at": _now(),
                "updated_at": _now(),
            }
            with STATE.lock:
                STATE.webhooks[hook_id] = record
                STATE.secrets[hook_id] = secret
            # The secret comes back exactly once, on creation.
            return self._send(201, {"status": "success", "data": dict(record, secret=secret)})

        if path == "/api/chatbot/ai-contexts":
            payload = json.loads(self._body() or b"{}")
            if STATE.refuse_tools:
                return self._send(403, {"status": "error", "data": None,
                                        "message": "You do not have permission to configure company endpoints"})
            config = payload.get("api_config") or {}
            url = config.get("url") or (config.get("tool") or {}).get("url") or ""
            if re.search(r"://(localhost|127\.|10\.|192\.168\.|169\.254\.)", url):
                return self._err(400, "api_config.url: private or loopback hosts are refused")
            context_id = str(uuid.uuid4())
            record = {
                "id": context_id,
                "name": payload.get("name"),
                "context_type": payload.get("context_type"),
                "api_config": config,
                "is_enabled": payload.get("enabled", True),
            }
            with STATE.lock:
                STATE.contexts[context_id] = record
            return self._send(201, {"status": "success", "data": record})

        if path == "/api/integrations/odoo/connect":
            payload = json.loads(self._body() or b"{}")
            url = (payload.get("webhook_url") or "").strip()
            if url and re.search(r"://(localhost|127\.|10\.|192\.168\.|169\.254\.)", url):
                return self._err(400, "callback_url must not be a private or loopback host")
            answer = {"webhook": {}, "tools": []}
            if url:
                with STATE.lock:
                    existing = next((w for w in STATE.webhooks.values() if w["url"] == url), None)
                    secret = payload.get("webhook_secret") or uuid.uuid4().hex
                    events = payload.get("events") or ["message.incoming", "button.reply"]
                    if existing:
                        existing.update({"events": events, "updated_at": _now()})
                        STATE.secrets[existing["id"]] = secret
                        answer["webhook"] = {"id": existing["id"], "status": "updated",
                                             "secret": secret, "events": events}
                    else:
                        hook_id = str(uuid.uuid4())
                        record = {"id": hook_id, "name": "Odoo connector", "url": url,
                                  "events": events, "headers": {"x-managed-by": "odoo_connector"},
                                  "is_active": True, "has_secret": True,
                                  "created_at": _now(), "updated_at": _now()}
                        STATE.webhooks[hook_id] = record
                        STATE.secrets[hook_id] = secret
                        answer["webhook"] = {"id": hook_id, "status": "created",
                                             "secret": secret, "events": events}
            for tool in payload.get("tools") or []:
                if STATE.refuse_tools:
                    answer["tools"].append({"name": tool.get("name"), "status": "failed",
                                            "message": "this plan does not include AI replies"})
                    continue
                config = dict(tool.get("api_config") or {}, managed_by="odoo_connector")
                context_id = str(uuid.uuid4())
                with STATE.lock:
                    STATE.contexts[context_id] = {
                        "id": context_id, "name": tool.get("name"),
                        "context_type": tool.get("context_type"),
                        "api_config": config, "is_enabled": tool.get("enabled", True),
                    }
                answer["tools"].append({"name": tool.get("name"), "status": "created"})
            return self._ok(answer)

        if path == "/api/templates":
            payload = json.loads(self._body() or b"{}")
            for required in ("whatsapp_account", "name", "language", "category"):
                if not payload.get(required):
                    return self._err(400, "whatsapp_account, name, language, and category are required")
            if any(t["name"] == payload["name"] for t in TEMPLATES) or payload["name"] in STATE.drafts:
                return self._err(500, "Failed to create template")
            body = payload.get("body_content") or ""
            # Meta's rule, reproduced because it is the one that bites: a body
            # may not begin or end with a placeholder.
            stripped = body.strip()
            if re.match(r"^\{\{\d+\}\}", stripped) or re.search(r"\{\{\d+\}\}[\s.،]*$", stripped):
                return self._err(502, "Failed to submit template to Meta: API error 100: Invalid "
                                      "parameter - variables must not be at the start or end")
            template_id = str(uuid.uuid4())
            record = dict(payload, id=template_id, status="DRAFT")
            with STATE.lock:
                STATE.drafts[payload["name"]] = record
            return self._ok(record)

        if path == "/api/templates/upload-media":
            ctype = self.headers.get("Content-Type", "")
            fields, upload = _parse_multipart(self._body(), ctype) if ctype.startswith("multipart/") else ({}, None)
            if not fields.get("account"):
                return self._err(400, "account is required")
            if not any(a["name"] == fields["account"] for a in ACCOUNTS):
                return self._err(400, "WhatsApp account not found")
            if not upload:
                return self._err(400, "No file provided")
            with STATE.lock:
                STATE.uploads.append(upload)
            return self._ok({"filename": upload["filename"], "handle": "4:" + uuid.uuid4().hex,
                             "mime_type": upload.get("content_type"), "size": upload["size"]})

        m = re.fullmatch(r"/api/templates/([^/]+)/publish", path)
        if m:
            with STATE.lock:
                record = next((t for t in STATE.drafts.values() if t["id"] == m.group(1)), None)
                if not record:
                    return self._err(404, "Template not found")
                if (record.get("header_type") or "") == "DOCUMENT" and not record.get("header_content"):
                    return self._err(400, "Template has DOCUMENT header but no media file has been "
                                          "uploaded. Please upload a sample document first.")
                record["status"] = "PENDING"
                return self._ok(record)

        if path == "/api/messages/template":
            return self._template_send()

        m = re.fullmatch(r"/api/contacts/([^/]+)/messages", path)
        if m:
            payload = json.loads(self._body() or b"{}")
            with STATE.lock:
                STATE.sent.append({"kind": "text", "contact_id": m.group(1), "payload": payload})
            return self._ok({"id": str(uuid.uuid4()), "status": "queued"})

        m = re.fullmatch(r"/api/webhooks/([^/]+)/test", path)
        if m:
            return self._ok({"delivered": True})

        return self._err(404, "404 page not found")

    def do_PUT(self):  # noqa: N802
        self._body()  # drained here so no early refusal can leave bytes behind
        if not self._authed():
            return
        path = self.path.split("?", 1)[0]
        m = re.fullmatch(r"/api/templates/([^/]+)", path)
        if m:
            payload = json.loads(self._body() or b"{}")
            with STATE.lock:
                record = next((t for t in STATE.drafts.values() if t["id"] == m.group(1)), None)
                if not record:
                    return self._err(404, "Template not found")
                record.update(payload)
                return self._ok(record)
        m = re.fullmatch(r"/api/chatbot/ai-contexts/([^/]+)", path)
        if m:
            payload = json.loads(self._body() or b"{}")
            with STATE.lock:
                record = STATE.contexts.get(m.group(1))
                if not record:
                    return self._err(404, "Context not found")
                record.update(payload)
                return self._ok(record)
        m = re.fullmatch(r"/api/webhooks/([^/]+)", self.path.split("?", 1)[0])
        if not m:
            return self._err(404, "404 page not found")
        payload = json.loads(self._body() or b"{}")
        with STATE.lock:
            record = STATE.webhooks.get(m.group(1))
            if not record:
                return self._err(404, "Webhook not found")
            record.update({k: v for k, v in payload.items() if k != "secret"})
            record["updated_at"] = _now()
            if payload.get("secret"):
                STATE.secrets[m.group(1)] = payload["secret"]
            return self._ok(record)

    def do_DELETE(self):  # noqa: N802
        self._body()  # drained here so no early refusal can leave bytes behind
        if not self._authed():
            return
        path = self.path.split("?", 1)[0]
        m = re.fullmatch(r"/api/chatbot/ai-contexts/([^/]+)", path)
        if m:
            with STATE.lock:
                STATE.contexts.pop(m.group(1), None)
            return self._ok({"deleted": True})
        m = re.fullmatch(r"/api/webhooks/([^/]+)", self.path.split("?", 1)[0])
        if not m:
            return self._err(404, "404 page not found")
        with STATE.lock:
            STATE.webhooks.pop(m.group(1), None)
            STATE.secrets.pop(m.group(1), None)
        return self._ok({"deleted": True})

    # -- the send path ----------------------------------------------------
    def _template_send(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        fields: dict[str, str] = {}
        header_file = None
        if ctype.startswith("multipart/form-data"):
            fields, header_file = _parse_multipart(self._body(), ctype)
        else:
            fields = json.loads(self._body() or b"{}")

        with STATE.lock:
            if STATE.fail_next > 0:
                STATE.fail_next -= 1
                return self._err(STATE.fail_status, "Upstream unavailable")

        name = fields.get("template_name")
        with STATE.lock:
            approved_draft = any(
                t["name"] == name and (t.get("status") or "").upper() == "APPROVED"
                for t in STATE.drafts.values()
            )
        # A template the connector wrote is sendable once Meta has approved it,
        # exactly like one that was always there. Anything still pending is not.
        if not any(t["name"] == name for t in TEMPLATES) and not approved_draft:
            return self._err(404, "Template not found")
        if not (fields.get("phone_number") or "").strip():
            return self._err(400, "phone_number is required")

        message_id = str(uuid.uuid4())
        with STATE.lock:
            STATE.sent.append(
                {
                    "kind": "template",
                    "id": message_id,
                    "fields": fields,
                    "header_file": header_file,
                    "at": _now(),
                }
            )
        return self._ok({"id": message_id, "status": "queued"})


def deliver(url: str, event: str, data: dict, secret: str) -> int:
    """Post a webhook the way the real dispatcher does — signed over raw bytes."""
    body = json.dumps({"event": event, "timestamp": _now(), "data": data}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={signature}",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--api-key", default="test-key")
    args = parser.parse_args()
    Handler.api_key = args.api_key
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake K-Message on http://127.0.0.1:{args.port} (key: {args.api_key})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
