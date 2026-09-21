# -*- coding: utf-8 -*-
{
    'name': 'K-Message Connector',
    # The Odoo Apps store wants the series in front of the version, and Odoo
    # refuses a version carrying the wrong one — '18.0.1.0.0' on a 17 server
    # is five components where the regex wants three, and it raises. So this
    # is the 18 manifest, and dev/build.py rewrites it for the 17 copy.
    'version': '18.0.1.0.0',
    'summary': 'Send documents on WhatsApp through K-Message, and let K-Message answer customer questions from Odoo',
    'author': 'Kerneltics',
    'website': 'https://k-message.kerneltics.com',
    'license': 'LGPL-3',
    'category': 'Marketing/WhatsApp',
    'depends': ['base', 'mail', 'phone_validation'],
    'data': [
        'security/kmessage_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'data/kmessage_capability_data.xml',
        'views/kmessage_account_views.xml',
        'views/kmessage_message_views.xml',
        'views/kmessage_template_views.xml',
        'views/kmessage_automation_views.xml',
        'views/kmessage_capability_views.xml',
        'views/kmessage_ai_tool_views.xml',
        'views/kmessage_token_views.xml',
        'views/kmessage_event_views.xml',
        'views/kmessage_reply_views.xml',
        'views/res_partner_views.xml',
        'wizard/kmessage_connect_views.xml',
        'wizard/kmessage_send_views.xml',
        'views/kmessage_menus.xml',
    ],
    # What the Apps store shows before anybody clicks.
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
