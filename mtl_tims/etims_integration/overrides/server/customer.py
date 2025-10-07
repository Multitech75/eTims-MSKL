import frappe
from frappe import _
from frappe.model.document import Document

from ...apis.apis import send_branch_customer_details
from ...utils import get_settings
from ...logger import etims_log

def on_update(doc: Document, method: str = None) -> None:
    company_name = frappe.defaults.get_user_default("Company") or frappe.get_value("Company", {}, "name")

    etims_log("Debug", "on_update company", company_name)
    settings = get_settings(company_name=company_name)
    if not settings:
        return
    
    etims_log("Debug", "on_update settings", settings,doc.name)

    # Submit to eTims only if conditions are satisfied and integration is active
    if (
        doc.custom_details_submitted_successfully == 0
        and doc.custom_prevent_etims_registration == 0
        and not doc.disabled
        and settings.get("is_active") == 1   # eTims Integration isActive
    ):
        send_branch_customer_details(doc.name, settings, True)


def validate(doc: Document, method: str = None) -> None:
    if getattr(doc, "require_tax_id", False):
        if not getattr(doc, "tax_id", None):
            frappe.throw(_("Tax ID is required"))
