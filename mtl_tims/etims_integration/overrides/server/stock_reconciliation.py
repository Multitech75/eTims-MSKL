import frappe
from frappe.model.document import Document

from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data

from ...apis.apis import send_payload_to_etims

from ...utils import  get_settings
from frappe.utils import now_datetime
from ...logger import etims_log
import time

def before_submit(doc: Document, method: str = None) -> None:
    settings_doc = get_settings()
    etims_log("Debug", "before_submit settings_doc", settings_doc)

    # FIX: Use frappe.throw instead of return to completely halt submission
    if not settings_doc:
        frappe.throw(
            "eTIMS Settings not found. Cannot proceed with submission.",
            title="eTIMS Configuration Missing"
        )

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
        # If eTIMS API call fails inside this function, 
        # make sure THAT function also uses frappe.throw() to stop the process.
        submit_stock_reconciliation(doc, settings_doc)

def submit_stock_reconciliation(doc: Document,settings_doc: dict | None) -> None:
    # Validate all items first
    for item in doc.items:
        item_doc = frappe.get_doc("Item", item.item_code)
        # Ensure item is eTims registered
        if not item_doc.custom_item_code_etims:
            from ...apis.apis import perform_item_registration
            perform_item_registration(item_doc.name)

            time.sleep(2) 
            #  # CRITICAL: Reload the document from the database to check if the API successfully saved the code
            # item_doc.reload()
            
            # # Enforce validation: Stop everything if the registration failed to yield a code
            # if not item_doc.custom_item_code_etims:
            #     frappe.throw(
            #         msg=f"Item <b>{item.item_name}</b> ({item.item_code}) failed eTIMS registration. <br>"
            #             f"Please register this item manually before submitting this document.",
            #         title="eTIMS Registration Failed"
            #     )

    # Build payload once
    payload = build_stock_reconciliation_payload(doc)
    # api_url = "http://41.139.135.45:8089/api/StockAdjustmentV2"
    api_url = f"{settings_doc.get('etims_url', '').rstrip('/')}/StockAdjustmentV2"
    api_key = settings_doc.get_password("api_key")
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
