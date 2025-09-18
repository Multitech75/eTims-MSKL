import frappe
from frappe.model.document import Document

from .shared_overrides import generic_invoices_before_submit
from ...utils import calculate_tax, get_settings
from ...logger import etims_log

def before_submit(doc: Document, method: str = None) -> None:
    """Check if company setting is active and items are eTims registered before submit."""

    settings_doc = get_settings()
    etims_log("Debug", "on_submit settings_doc", settings_doc)

    if not settings_doc:
        return
    if doc.is_pos:  # This is True for POS invoices
            etims_log("Debug", "POS Invoice detected", doc.name)
    else:
        etims_log("Debug", "Normal Sales Invoice", doc.name)
    # Ensure all items are registered in eTims
    for item in doc.items:
        item_doc = frappe.get_doc("Item", item.item_code)
        etims_log("Debug", "_set_taxation_type_codes item_doc", item_doc.name)

        if not item_doc.custom_item_code_etims:
            frappe.throw(
                f"Item {item.item_name} is not registered in eTims. Invoice cannot be submitted."
            )

    calculate_tax(doc)

    # Submit to eTims only if conditions are satisfied and integration is active
    if (
        doc.custom_successfully_submitted == 0
        and doc.prevent_etims_submission == 0
        and doc.is_opening == "No"
        and settings_doc.get("is_active") == 1   # eTims Integration isActive
    ):
        generic_invoices_before_submit(doc, settings_doc,"Sales Invoice")


def before_cancel(doc: Document, method: str = None) -> None: 
    """Disallow cancelling of submitted invoice to eTIMS."""

    etims_log("Debug", "before_cancel", doc.as_dict())

    if doc.doctype == "Sales Invoice" and doc.custom_successfully_submitted:
        frappe.throw(
            "This invoice has already been <b>submitted</b> to eTIMS and cannot be <span style='color:red'>Canceled.</span>\n"
            "If you need to make adjustments, please create a Credit Note instead."
        )
    elif doc.doctype == "Purchase Invoice" and doc.custom_submitted_successfully:
        frappe.throw(
            "This invoice has already been <b>submitted</b> to eTIMS and cannot be <span style='color:red'>Canceled.</span>.\n"
            "If you need to make adjustments, please create a Debit Note instead."
        )

