import frappe
import frappe.defaults
from frappe.model.document import Document

from ...apis.apis import submit_item_composition
from ...utils import get_settings

def on_submit(doc: Document, method: str = None) -> None:
    """Item doctype before insertion hook"""
    company_name = (
        doc.company
        or frappe.defaults.get_user_default("Company")
        or frappe.get_value("Company", {}, "name")
    )
    settings = get_settings(company_name=company_name)

    if not settings: 
        return

    submit_item_composition(doc.name)
