import frappe
from frappe.model.document import Document

from ...apis.apis import send_branch_customer_details
from ...utils import get_active_settings


def on_update(doc: Document, method: str = None) -> None:
    active_settings = get_active_settings()
    if not active_settings:
        return
        
    # Submit to eTims only if conditions are satisfied and integration is active
    if (
        doc.custom_details_submitted_successfully == 0
        and doc.custom_prevent_etims_registration == 0
        and settings_doc.get("is_active") == 1   # eTims Integration isActive
    ):
        send_branch_customer_details(doc.name, active_settings, False)
