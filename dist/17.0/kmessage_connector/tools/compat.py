# -*- coding: utf-8 -*-
"""The handful of places Odoo 17 and Odoo 18 disagree.

Odoo 18 renamed the list view's tag and view type from ``tree`` to ``list``.
XML cannot ask which server it is running on — that is what ``dev/build.py``
is for — but Python can, so anything built at runtime asks here instead of
hardcoding a name that is wrong on half the supported versions.
"""

from odoo import release

ODOO_MAJOR = release.version_info[0]

#: The name of the list view type on this server.
LIST = 'list' if ODOO_MAJOR >= 18 else 'tree'

#: The usual `view_mode` for an action that opens a list and a form.
LIST_FORM = '%s,form' % LIST
