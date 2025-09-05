import frappe
from frappe import _
from frappe.model.document import Document

from ...apis.apis import send_branch_customer_details
from ...doctype.doctype_names_mapping import SLADE_ID_MAPPING_DOCTYPE_NAME
from ...utils import get_active_settings


def on_update(doc: Document, method: str = None) -> None:
    active_settings = get_active_settings()
    for setting in active_settings:
        setup_mapping = frappe.db.get_value(
            SLADE_ID_MAPPING_DOCTYPE_NAME,
            {"parent": doc.name, "etims_setup": setting.name},
            "name"
        )
        
        if not setup_mapping:
            send_branch_customer_details(doc.name, setting.name)

def validate(doc: Document, method: str = None) -> None:
    if getattr(doc, "require_tax_id", False):
        if not getattr(doc, "tax_id", None):
            frappe.throw(_("Tax ID is required"))
