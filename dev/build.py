#!/usr/bin/env python3
"""Produce the Odoo 17 copy of the addon from the Odoo 18 source.

Odoo 18 renamed the list view: the XML tag is ``<list>`` and the view type is
``list``, where 17 has ``<tree>`` and ``tree``. A manifest cannot branch on the
running version — Odoo reads it with ``ast.literal_eval`` — and one XML file
cannot satisfy both servers, so the source is written for 18 and this script
mechanically produces the 17 flavour.

It rewrites only what is unambiguous: the view tags, the ``view_mode`` of an
action, a ``mode=`` on a field, and the ``*_view_ref`` context keys. Python is
copied untouched, because Python asks ``tools/compat.py`` which server it is on.

    python3 dev/build.py                 # every module, into dist/17.0/
    python3 dev/build.py --check         # verify dist/17.0 matches the source

The result is a directory you add to an Odoo 17 addons path exactly as you
would add the repository root to an Odoo 18 one.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist' / '17.0'

SKIP_DIRS = {'__pycache__', '.git', 'dist', 'dev', 'docs', '.idea', '.vscode'}

#: Each rule is (pattern, replacement). Anchored on the syntax, never on prose:
#: "<list" can only be a tag, while the word "list" in a help string is left be.
RULES = [
    (re.compile(r'<list(?=[\s/>])'), '<tree'),
    (re.compile(r'</list>'), '</tree>'),
    (re.compile(r'(<field name="view_mode">)([^<]*)(</field>)'),
     lambda m: m.group(1) + m.group(2).replace('list', 'tree') + m.group(3)),
    (re.compile(r'mode="([^"]*)"'),
     lambda m: 'mode="%s"' % m.group(1).replace('list', 'tree')),
    (re.compile(r'\blist_view_ref\b'), 'tree_view_ref'),
]


def is_module(path: Path) -> bool:
    return (path / '__manifest__.py').is_file()


def convert_xml(text: str) -> str:
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    return text


#: The series this tree is written for, and the one it is built into.
SOURCE_SERIES = '18.0'
TARGET_SERIES = '17.0'


def convert_manifest(text: str) -> str:
    """Point the version at the series this copy is for.

    Odoo refuses a version carrying the wrong series outright: ``18.0.1.0.0``
    on a 17 server is five components where its regex wants three, and it
    raises before the module is even read. The Apps store wants the prefix, so
    each copy has to carry its own.
    """
    return text.replace("'version': '%s." % SOURCE_SERIES,
                        "'version': '%s." % TARGET_SERIES)


def build_module(source: Path, target: Path) -> tuple[int, int]:
    if target.exists():
        shutil.rmtree(target)
    files = converted = 0
    for item in sorted(source.rglob('*')):
        if any(part in SKIP_DIRS for part in item.relative_to(source).parts):
            continue
        destination = target / item.relative_to(source)
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        files += 1
        if item.suffix == '.xml':
            original = item.read_text(encoding='utf-8')
            rewritten = convert_xml(original)
            destination.write_text(rewritten, encoding='utf-8')
            if rewritten != original:
                converted += 1
        elif item.name == '__manifest__.py':
            original = item.read_text(encoding='utf-8')
            rewritten = convert_manifest(original)
            destination.write_text(rewritten, encoding='utf-8')
            if rewritten != original:
                converted += 1
        else:
            shutil.copy2(item, destination)
    return files, converted


def check_manifest(module: Path) -> list[str]:
    """Catch the manifest mistake that only shows up on the other version."""
    problems = []
    text = (module / '__manifest__.py').read_text(encoding='utf-8')
    version = re.search(r"'version'\s*:\s*'([^']+)'", text)
    if not version:
        problems.append('%s: no version in the manifest' % module.name)
    elif not version.group(1).startswith(SOURCE_SERIES + '.'):
        problems.append(
            "%s: version %r does not start with %s — the Apps store wants the "
            "series in front, and this copy is the %s one"
            % (module.name, version.group(1), SOURCE_SERIES, SOURCE_SERIES))
    elif not re.match(r'^\d\d\.0\.\d+\.\d+\.\d+$', version.group(1)):
        problems.append(
            "%s: version %r is not series.x.y.z" % (module.name, version.group(1)))

    name = re.search(r"'name'\s*:\s*'([^']+)'", text)
    if not name:
        problems.append('%s: no name in the manifest' % module.name)
    elif len(name.group(1)) > 25:
        # apps.odoo.com's vendor guidelines: explicit, at most 25 characters.
        problems.append("%s: name %r is %d characters; the store wants 25 or fewer"
                        % (module.name, name.group(1), len(name.group(1))))

    for key in ("'license'", "'author'", "'website'", "'summary'", "'category'"):
        if key not in text:
            problems.append('%s: the Apps store wants %s in the manifest' % (module.name, key))

    listing = module / 'static' / 'description' / 'index.html'
    if not listing.exists():
        problems.append('%s: no static/description/index.html for the store page' % module.name)
    icon = module / 'static' / 'description' / 'icon.png'
    if not icon.exists():
        problems.append('%s: no static/description/icon.png' % module.name)

    # Every file named in 'images' has to be there, or the store page renders
    # a broken thumbnail — which is worse than having no cover at all.
    for relative in re.findall(r"'images'\s*:\s*\[([^\]]*)\]", text):
        for name in re.findall(r"'([^']+)'", relative):
            if not (module / name).exists():
                problems.append("%s: 'images' names %s, which is not there"
                                % (module.name, name))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='report what would change instead of writing')
    args = parser.parse_args()

    modules = [path for path in sorted(ROOT.iterdir()) if path.is_dir() and is_module(path)]
    if not modules:
        print('No modules found in %s' % ROOT, file=sys.stderr)
        return 1

    problems = []
    for module in modules:
        problems.extend(check_manifest(module))

    if args.check:
        for module in modules:
            for xml in sorted(module.rglob('*.xml')):
                text = xml.read_text(encoding='utf-8')
                if convert_xml(text) != text:
                    print('would convert %s' % xml.relative_to(ROOT))
        for problem in problems:
            print('PROBLEM %s' % problem, file=sys.stderr)
        return 1 if problems else 0

    if problems:
        for problem in problems:
            print('PROBLEM %s' % problem, file=sys.stderr)
        return 1

    DIST.mkdir(parents=True, exist_ok=True)
    for module in modules:
        files, converted = build_module(module, DIST / module.name)
        print('%-34s %3d files, %d converted for 17.0' % (module.name, files, converted))
    print('\nOdoo 17 copy is in %s' % DIST.relative_to(ROOT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
