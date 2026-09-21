#!/bin/sh
# Rebuild the 17.0 branch from this one.
#
# The 17 modules are generated — <list> becomes <tree>, the manifest version
# changes series — so the branch that carries them is generated too. Keeping
# it by hand would mean two trees drifting apart, which is the thing the
# build script exists to prevent.
#
# Run it from a clean 18.0 (or main) checkout:  sh dev/publish_17.sh
set -e

MODULES="kmessage_connector kmessage_connector_account kmessage_connector_sale kmessage_connector_stock"
START=$(git rev-parse --abbrev-ref HEAD)

test -z "$(git status --porcelain)" || {
    echo "The working tree is dirty. Commit first — this branch is thrown away and rebuilt." >&2
    exit 1
}

python3 dev/build.py
git add -A && git diff --cached --quiet || git commit -m "Rebuild dist/17.0"

git checkout -B 17.0 "$START"
for m in $MODULES; do rm -rf "$m"; mv "dist/17.0/$m" "$m"; done
rm -rf dist dev
cat > BUILD.md <<'NOTE'
# This branch is generated

The source lives on [`18.0`](../../tree/18.0). Odoo 18 renamed the list view's
tag — `<list>` where 17 has `<tree>` — and refuses a manifest version carrying
the other series, so one tree cannot install on both. `dev/build.py` on the
18 branch rewrites both and produces exactly what is here.

Fix things on `18.0`. A change made here is overwritten by the next build.
NOTE

git add -A
git commit -q -m "Rebuild the 17.0 branch from $START"
git push -f origin 17.0
git checkout -q "$START"
echo "17.0 rebuilt from $START and pushed"
