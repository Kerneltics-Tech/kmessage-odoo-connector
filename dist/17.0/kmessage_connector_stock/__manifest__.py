# -*- coding: utf-8 -*-
{
    'name': 'K-Message Connector — Inventory',
    # The Odoo Apps store wants the series in front of the version, and Odoo
    # refuses a version carrying the wrong one — '18.0.1.0.0' on a 17 server
    # is five components where the regex wants three, and it raises. So this
    # is the 18 manifest, and dev/build.py rewrites it for the 17 copy.
    'version': '17.0.1.0.0',
    'summary': "Tell a customer their delivery has left, and answer “do you have it in stock?”",
    'author': 'Kerneltics',
    'website': 'https://k-message.kerneltics.com',
    'license': 'LGPL-3',
    'category': 'Marketing/WhatsApp',
    'depends': ['kmessage_connector', 'stock'],
    'data': [
        'views/kmessage_send_action.xml',
        'data/kmessage_capability_data.xml',
        'data/kmessage_ai_tool_data.xml',
        'views/kmessage_capability_views.xml',
    ],
    'installable': True,
    # Installs itself next to Inventory: a customer installs the connector and
    # gets exactly the bridges their Odoo can use.
    'auto_install': True,
}
