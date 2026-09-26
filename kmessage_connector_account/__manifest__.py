# -*- coding: utf-8 -*-
{
    'name': 'K-Message Invoicing',
    # The Odoo Apps store wants the series in front of the version, and Odoo
    # refuses a version carrying the wrong one — '18.0.1.0.0' on a 17 server
    # is five components where the regex wants three, and it raises. So this
    # is the 18 manifest, and dev/build.py rewrites it for the 17 copy.
    'version': '17.0.1.0.0',
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
    # What the Apps store shows before anybody clicks.
    'images': ['static/description/banner.png'],
    'installable': True,
    # Whoever has both the connector and accounting wants the two joined up;
    # nothing here sends or answers anything until a switch is turned on.
    'auto_install': True,
}
