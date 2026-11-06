import frappe
from frappe.utils import now_datetime


def execute() -> None:
    updated_at = now_datetime()
    names = frappe.get_all("eTims Unit Packing", pluck="name")

    for name in names:
        frappe.db.set_value(
            "eTims Unit Packing",
            name,
            {
                "locationid": "01",
                "datecreated": updated_at,
                "recordchanged": 1,
                "isvalid": 1,
            },
            update_modified=False,
        )
