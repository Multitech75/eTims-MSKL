import frappe
from frappe.model.document import Document
from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data
from ...apis.apis import send_payload_to_etims
from ...utils import get_settings
from frappe.utils import now_datetime
from ...logger import etims_log

def before_submit(doc: Document, method: str = None) -> None:
    settings_doc = get_settings()
    etims_log("Debug", "before_submit settings_doc", settings_doc)

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

    # 2. Guard: Avoid running eTIMS integration if core valuation rates are broken or missing
    if hasattr(doc, 'items'):
        for item in doc.items:
            if not item.valuation_rate or item.valuation_rate <= 0:
                # Return and exit cleanly. This allows ERPNext's core validation 
                # layer to gracefully throw the native "Valuation Rate required" error.
                return

    # 3. Submit stock reconciliation only if allowed
    if (
        doc.custom_submitted_successfully != 1
        and doc.custom_prevent_etims_registration != 1
        and settings_doc.get("is_active") == 1   # eTims Integration isActive
    ):
        submit_stock_reconciliation(doc, settings_doc)


def submit_stock_reconciliation(doc: Document, settings_doc: dict | None) -> None:
    # Validate and register all items first
    for item in doc.items:
        # Optimization: Fetch only the specific field directly from the DB bypassing cache
        custom_item_code = frappe.db.get_value("Item", item.item_code, "custom_item_code_etims")
        
        # Ensure item is eTims registered
        if not custom_item_code:
            from ...apis.apis import perform_item_registration
            
            # Trigger item registration (Ensure this function performs a database commit/save natively)
            perform_item_registration(item.item_code)

            # Re-fetch directly from the DB transaction stream without using time.sleep()
            custom_item_code = frappe.db.get_value("Item", item.item_code, "custom_item_code_etims")
            
            # Enforce validation: Stop everything if the registration failed to yield a code
            if not custom_item_code:
                frappe.throw(
                    msg=f"Item <b>{item.item_name}</b> ({item.item_code}) failed eTIMS registration. <br>"
                        f"Please register this item manually before submitting this document.",
                    title="eTIMS Registration Failed"
                )

    # Build payload once
    payload = build_stock_reconciliation_payload(doc)
    api_url = f"{settings_doc.get('etims_url', '').rstrip('/')}/StockAdjustmentV2"
    api_key = settings_doc.get_password("api_key")
    response = send_payload_to_etims(payload, api_url, api_key)
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
        # Directly fetch required fields via DB to optimize execution speed
        item_data = frappe.db.get_values("Item", item.item_code, ["custom_e_tims_tax_code", "item_name"], as_dict=True)
        tax_code = item_data[0].get("custom_e_tims_tax_code") if item_data else ""
        
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
