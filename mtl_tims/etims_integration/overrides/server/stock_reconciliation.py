import frappe
from frappe.model.document import Document

from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data

from ...apis.api_builder import EndpointsBuilder
from ...apis.process_request import process_request
from ...apis.apis import send_invoice_to_etims
from ...apis.remote_response_status_handlers import (
    purchase_invoice_submission_on_success,
)
from ...utils import get_taxation_types, get_settings
from frappe.utils import now_datetime
from ...logger import etims_log

endpoints_builder = EndpointsBuilder()

def before_submit(doc: Document, method: str = None) -> None:
   if (
        doc.custom_submitted_successfully != 1
        and doc.custom_prevent_etims_registration != 1
    ):
        submit_stock_reconciliation(doc)


def submit_stock_reconciliation(doc: Document) -> None:
    # Validate all items first
    for item in doc.items:
        item_doc = frappe.get_doc("Item", item.item_code)
        if not item_doc.custom_item_code_etims:
            frappe.throw(
                f"Item {item.item_name} is not registered in eTims. "
                "Stock Reconciliation cannot be submitted."
            )

    # Build payload once
    payload = build_stock_reconciliation_payload(doc)

    # api_url = frappe.db.get_single_value("ETims Settings", "stock_reconciliation_url") \
    #     or "http://41.139.35.45:8089/api/StockAdjustmentV2"

    api_url = "http://41.139.135.45:8089/api/StockAdjustmentV2"
    response = send_invoice_to_etims(payload, api_url)
    etims_log("Debug", "submit_stock_reconciliation response", response)

    if not response.get("status"):
        frappe.throw(
            msg=f"Failed to validate Stock Reconciliation {doc.name} in eTims.<br>{response.get('message')}",
            title="eTims Error"
        )
    else:
        doc.custom_submitted_successfully = 1
        doc.custom_stock_reconciliation_eTims_message = response.get("message")
        doc.custom_eTims_response = frappe.as_json(response)


def build_stock_reconciliation_payload(doc: Document) -> dict:
    payload = {
        "storeReleaseTypeCode": "06",
        "remark": "MSKL",
        "mapping": doc.name,
        "stockItemList": []
    }

    for item in doc.items:
        item_doc = frappe.get_doc("Item", item.item_code)
        tax_code = item_doc.custom_eTims_tax_code or ""
        if not tax_code:
            frappe.throw(
                msg=f"Item {item.item_name} does not have a valid eTims Tax Code. "
                    "Please update the item before submitting.",
                title="eTims Error"
            )

        qty = abs(item.get("qty"))
        payload["stockItemList"].append({
            "itemCode": item.item_code,
            "packageQuantity": qty,
            "quantity": qty
        })

    etims_log("Debug", "submit_stock_reconciliation payload", frappe.as_json(payload))
    return payload
