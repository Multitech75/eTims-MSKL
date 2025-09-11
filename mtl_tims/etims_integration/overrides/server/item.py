import frappe
import frappe.defaults
from frappe import _
from frappe.model.document import Document

from ...apis.apis import perform_item_registration
from ...doctype.doctype_names_mapping import SETTINGS_DOCTYPE_NAME
from ...utils import  get_settings
from ...logger import etims_log

def on_update(doc: Document, method: str = None) -> None:
    """Item doctype before insertion hook"""
    """ Check if ETIMS integration is active """
    settings_doc = get_settings()
    etims_log("Debug", "on_submit settings_doc", settings_doc)

    if not settings_doc:
        return
    
    if (
        not doc.custom_item_code_etims
        and doc.custom_item_registered != 1
        and doc.custom_prevent_etims_registration != 1
        and not doc.disabled
        and settings_doc.get("is_active") == 1   # eTims Integration isActive
    ):
        perform_item_registration(doc.name)



@frappe.whitelist()
def prevent_item_deletion(doc: dict) -> None:
    if not frappe.db.exists(SETTINGS_DOCTYPE_NAME, {"is_active": 1}):
        return
    if (doc.custom_item_registered == 1 and doc.custom_item_code_etims):  # Assuming 1 means registered, adjust as needed
        frappe.throw(_("Cannot delete registered items"))
    pass
