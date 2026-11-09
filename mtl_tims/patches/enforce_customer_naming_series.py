import frappe
from frappe.model.naming import make_autoname
from frappe.model.rename_doc import rename_doc

from mtl_tims.etims_integration.overrides.server.customer import (
    CUSTOMER_NAME_PATTERN,
    CUSTOMER_NAMING_SERIES,
)


def execute() -> None:
    frappe.make_property_setter(
        {
            "doctype": "Customer",
            "doctype_or_field": "DocField",
            "fieldname": "naming_series",
            "property": "options",
            "value": CUSTOMER_NAMING_SERIES,
            "property_type": "Text",
        }
    )
    frappe.make_property_setter(
        {
            "doctype": "Customer",
            "doctype_or_field": "DocField",
            "fieldname": "naming_series",
            "property": "default",
            "value": CUSTOMER_NAMING_SERIES,
            "property_type": "Data",
        }
    )
    frappe.defaults.set_global_default("cust_master_name", "Naming Series")

    customer_names = frappe.get_all(
        "Customer", pluck="name", order_by="creation asc, name asc"
    )
    for old_name in customer_names:
        new_name = make_autoname(CUSTOMER_NAMING_SERIES)
        if not CUSTOMER_NAME_PATTERN.fullmatch(new_name):
            frappe.throw(
                "The C-.###### Customer naming series is exhausted; "
                "cannot safely rename all existing Customers."
            )
        while frappe.db.exists("Customer", new_name):
            new_name = make_autoname(CUSTOMER_NAMING_SERIES)
            if not CUSTOMER_NAME_PATTERN.fullmatch(new_name):
                frappe.throw(
                    "The C-.###### Customer naming series is exhausted; "
                    "cannot safely rename all existing Customers."
                )

        rename_doc(
            "Customer",
            old_name,
            new_name,
            force=True,
            ignore_permissions=True,
            show_alert=False,
            rebuild_search=False,
        )
        frappe.db.set_value(
            "Customer",
            new_name,
            "naming_series",
            CUSTOMER_NAMING_SERIES,
            update_modified=False,
        )

    frappe.clear_cache(doctype="Customer")
