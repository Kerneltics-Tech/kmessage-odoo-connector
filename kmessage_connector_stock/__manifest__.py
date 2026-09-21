# -*- coding: utf-8 -*-
{
    'name': 'K-Message Connector — Inventory',
    # No series prefix on purpose: Odoo stamps the running series onto it, so
    # the same source installs on 17.0 and 18.0 without a second manifest.
    'version': '1.0.0',
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
