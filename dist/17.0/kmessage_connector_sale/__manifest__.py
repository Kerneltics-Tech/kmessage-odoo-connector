# -*- coding: utf-8 -*-
{
    'name': 'K-Message Connector — Sales',
    # No series prefix, for the reason core gives: Odoo stamps the running
    # series onto it, and one source tree installs on 17.0 and 18.0.
    # The Odoo Apps store wants the series in front of the version, and Odoo
    # refuses a version carrying the wrong one — '18.0.1.0.0' on a 17 server
    # is five components where the regex wants three, and it raises. So this
    # is the 18 manifest, and dev/build.py rewrites it for the 17 copy.
    'version': '17.0.1.0.0',
    'summary': 'Tell customers on WhatsApp when their order moves, and let them ask where it stands',
    'description': """
Sales for the K-Message connector
=================================

Adds two triggers an automation can listen for — a quotation was sent, an order
was confirmed — and two questions K-Message may ask Odoo on a customer's behalf:
their recent orders, and the state of one order they name.

Installing this sends nothing. Connecting writes two rules for it — quotation
sent, order confirmed — filled in and **switched off**, because telling every
customer about their quotation is a decision only the company can make. Read
one and flip its toggle. The two assistant tools are drafted against the
connection and published to K-Message when the connection is set up.
""",
    'author': 'Kerneltics',
    'website': 'https://k-message.kerneltics.com',
    'license': 'LGPL-3',
    'category': 'Marketing/WhatsApp',
    'depends': ['kmessage_connector', 'sale'],
    'data': [
        'views/kmessage_send_action.xml',
        'data/kmessage_capability_data.xml',
        'data/kmessage_ai_tool_data.xml',
    ],
    'installable': True,
    'auto_install': True,
}
