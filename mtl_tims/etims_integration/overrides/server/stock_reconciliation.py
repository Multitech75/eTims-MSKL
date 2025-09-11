import frappe
from frappe.model.document import Document

from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data

from ...apis.api_builder import EndpointsBuilder
from ...apis.process_request import process_request
from ...apis.apis import send_payload_to_etims
from ...apis.remote_response_status_handlers import (
    purchase_invoice_submission_on_success,
)
from ...utils import get_taxation_types, get_settings
from frappe.utils import now_datetime
from ...logger import etims_log

endpoints_builder = EndpointsBuilder()
def before_submit(doc: Document, method: str = None) -> None:
    settings_doc = get_settings()
    etims_log("Debug", "before_submit settings_doc", settings_doc)

    if not settings_doc:
        # No settings found → stop further processing
        return

    # 1. Validate warehouse
    if settings_doc.get("default_warehouse") and settings_doc.get("default_warehouse") != doc.set_warehouse:
        frappe.throw(
            f"Purchase Invoice Warehouse must be the default warehouse {settings_doc.get('default_warehouse')} set in eTims Settings."
        )

    # 2. Submit stock reconciliation only if allowed
    if (
        doc.custom_submitted_successfully != 1
        and doc.custom_prevent_etims_registration != 1
        and settings_doc.get("is_active") == 1   # eTims Integration isActive
    ):
        submit_stock_reconciliation(doc, settings_doc)


def submit_stock_reconciliation(doc: Document,settings_doc: dict | None) -> None:
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
    # api_url = "http://41.139.135.45:8089/api/StockAdjustmentV2"
    api_url = f"{settings_doc.get('etims_url', '').rstrip('/')}/StockAdjustmentV2"
    api_key = settings_doc.get("api_key")#"rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"
    response = send_payload_to_etims(payload, api_url,api_key)
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
