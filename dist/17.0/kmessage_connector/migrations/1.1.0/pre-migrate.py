# -*- coding: utf-8 -*-
"""A company may now hold more than one connection.

Taking a constraint out of the code does not take it out of the database:
Odoo drops one only when the module is uninstalled. So it is dropped here,
along with the record that describes it, or the second connection would
still be refused by a rule nobody can see any more.

Named without the series so the same folder serves the 17.0 build.
"""


def migrate(cr, version):
    cr.execute(
        "ALTER TABLE kmessage_account "
        "DROP CONSTRAINT IF EXISTS kmessage_account_company_unique")
    cr.execute(
        "DELETE FROM ir_model_constraint "
        "WHERE name = 'kmessage_account_company_unique'")
    cr.execute(
        "DELETE FROM ir_model_data WHERE module = 'kmessage_connector' "
        "AND name = 'constraint_kmessage_account_company_unique'")
