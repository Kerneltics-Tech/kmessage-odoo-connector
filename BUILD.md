# This branch is generated

The source lives on [`18.0`](../../tree/18.0). Odoo 18 renamed the list view's
tag — `<list>` where 17 has `<tree>` — and refuses a manifest version carrying
the other series, so one tree cannot install on both. `dev/build.py` on the
18 branch rewrites both and produces exactly what is here.

Fix things on `18.0`. A change made here is overwritten by the next build.
