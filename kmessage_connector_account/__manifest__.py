# -*- coding: utf-8 -*-
{
    'name': 'K-Message Connector — Invoicing',
    # No series prefix on purpose: Odoo stamps the running series onto it, so
    # the same source installs on 17.0 and 18.0 without a second manifest.
    'version': '1.0.0',
    'summary': 'Send invoices on WhatsApp, and let a customer ask what they owe',
    'author': 'Kerneltics',
    'website': 'https://k-message.kerneltics.com',
    'license': 'LGPL-3',
    'category': 'Marketing/WhatsApp',
    'depends': ['kmessage_connector', 'account'],
    'data': [
        'views/kmessage_send_action.xml',
        'data/kmessage_capability_data.xml',
        'data/ir_cron.xml',
        'data/kmessage_ai_tool_data.xml',
        'views/kmessage_account_views.xml',
    ],
    'installable': True,
    # Whoever has both the connector and accounting wants the two joined up;
    # nothing here sends or answers anything until a switch is turned on.
    'auto_install': True,
}
