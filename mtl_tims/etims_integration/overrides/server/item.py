import frappe
import frappe.defaults
from frappe import _
from frappe.model.document import Document

from ...apis.apis import perform_item_registration
# from ...doctype.doctype_names_mapping import SETTINGS_DOCTYPE_NAME, SLADE_ID_MAPPING_DOCTYPE_NAME
# from ...utils import generate_custom_item_code_etims, get_settings
from ...logger import etims_log

def on_update(doc: Document, method: str = None) -> None:
    """Item doctype before insertion hook"""
    """ Check if ETIMS integration is active """
    # active_settings= frappe.db.exists(SETTINGS_DOCTYPE_NAME, {"is_active": 1})
    # if not active_settings:
    #     return

    # company_name = (
    #     doc.company
    #     or frappe.defaults.get_user_default("Company")
    #     or frappe.get_value("Company", {}, "name")
    # )

    # etims_log("Debug", "on_update company_name", doc)
    # etims_log("Debug", "on_update doc name", doc.name)
    # etims_log("Debug", "on_update doc custom_item_eTims_message", doc.custom_item_eTims_message)
    if doc.custom_item_code_etims and doc.custom_item_registered == 1 and doc.custom_prevent_etims_registration == 1 and doc.disabled:
        return

    perform_item_registration(doc.name)



# @frappe.whitelist()
# def prevent_item_deletion(doc: dict) -> None:
#     if not frappe.db.exists(SETTINGS_DOCTYPE_NAME, {"is_active": 1}):
#         return
#     if doc.custom_item_registered == 1:  # Assuming 1 means registered, adjust as needed
#         frappe.throw(_("Cannot delete registered items"))
#     pass
