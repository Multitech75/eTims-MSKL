from frappe.model.document import Document

from .shared_overrides import generic_invoices_before_submit
from ...utils import calculate_tax, get_settings
from ...logger import etims_log



def before_submit(doc: Document, method: str) -> None:
    """Intercepts POS invoice on submit event"""

    settings_doc = get_settings()
    etims_log("Debug", "before_submit settings_doc", settings_doc)

    if not settings_doc:
        return
    if not doc.custom_successfully_submitted:
        generic_invoices_before_submit(doc, settings_doc,"POS Invoice")
