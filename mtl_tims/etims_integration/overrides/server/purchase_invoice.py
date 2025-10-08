import frappe
from frappe.model.document import Document

from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data

from ...apis.apis import send_payload_to_etims

from ...utils import get_settings
from frappe.utils import now_datetime
from ...logger import etims_log


def validate(doc: Document, method: str = None) -> None:
    get_itemised_tax_breakup_data(doc)
    if not doc.taxes:
        vat_acct = frappe.get_value(
            "Account", {"account_type": "Tax", "tax_rate": "16"}, ["name"], as_dict=True
        )
        doc.set(
            "taxes",
            [
                {
                    "account_head": vat_acct.name,
                    "included_in_print_rate": 1,
                    "description": vat_acct.name.split("-", 1)[0].strip(),
                    "category": "Total",
                    "add_deduct_tax": "Add",
                    "charge_type": "On Net Total",
                }
            ],
        )


def before_submit(doc: Document, method: str = None) -> None:
    
    settings_doc = get_settings()
    etims_log("Debug", "on_submit settings_doc", settings_doc)

    if not settings_doc:
        return
    if (
        doc.custom_submitted_successfully != 1
        and doc.prevent_etims_submission != 1
        and settings_doc.get("is_active") == 1   # eTims Integration isActive
    ):
        submit_purchase_invoice(doc,settings_doc)


def submit_purchase_invoice(doc: Document,settings_doc: dict | None) -> None:
    for item in doc.items:
        item_doc = frappe.get_doc("Item", item.item_code)
        etims_log("Debug", "_set_taxation_type_codes item_doc", item_doc.name)

        # Ensure item is eTims registered
        if not item_doc.custom_item_code_etims:
            frappe.throw(
                f"Item {item.item_name} is not registered in eTims. Purchase Invoice cannot be submitted."
            )

    if not doc.is_return:
        payload = build_purchase_invoice_payload(doc)
        # api_url = "http://41.139.135.45:8089/api/AddPurchaseV2"
        api_url = f"{settings_doc.get('etims_url', '').rstrip('/')}/AddPurchaseV2"
        api_key = settings_doc.get("api_key")#"rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"
        response = send_payload_to_etims(payload, api_url,api_key)

        etims_log("Debug", "generic_invoices_on_submit_override response", response)
        # check if response is Purchase already exist then set custom_submitted_successfully to 1
        
        # if not response.get("status"):
        if response.get("status") or "Purchase already exist" in (response.get("message")):
            doc.custom_submitted_successfully = 1
            doc.custom_purchase_invoice_eTims_message = response.get("message")
            doc.custom_eTims_response = frappe.as_json(response)

            etims_log("Debug", "Parent Invoice updated fields", doc.as_dict())
    
    
            # --- Update Purchase Invoice Items (Child Table) ---
            etims_log("Debug", "Item Responses Empty - Populating from Item master")
            etims_log("Debug", f"Items count: {len(doc.items)}")

            for i, item in enumerate(doc.items):
                etims_log("Debug", f"Updating Item {i}: {item.item_code}")
                etims_log("Debug", f"Updating Item before {i}: {item.as_dict()}")

                # Fetch from Item master
                item_doc = frappe.get_doc("Item", item.item_code)

                # Populate custom fields from the Item doctype
                item.custom_total_amount = (item.custom_tax_amount or 0) + (item.base_net_amount or 0)
                item.custom_item_code_etims = item_doc.get("custom_item_code_etims")
                item.custom_item_classification = item_doc.get("custom_item_classification")
                item.custom_item_classification_level = item_doc.get("custom_item_classification_level")
                item.custom_item_classification_code = item_doc.get("custom_item_classification_code")
                item.custom_etims_country_of_origin = item_doc.get("custom_etims_country_of_origin")
                item.custom_packaging_unit = item_doc.get("custom_packaging_unit")
                item.custom_unit_of_quantity = item_doc.get("custom_unit_of_quantity")
                item.taxation_type_code = item_doc.get("custom_eTims_tax_code")

                # Log update
                etims_log("Debug", f"Item {i} updated fields", item.as_dict())
            
            # --- Save and commit ---
            # doc.save(ignore_permissions=True)
            # frappe.db.commit()
        
        else:
            frappe.throw(
                    msg=f"Failed to validate purchase invoice {doc.name} in eTims.<br>{response.get('message')}",
                    title="eTims Error"
                )



def build_purchase_invoice_payload(doc: Document) -> dict:
    dt = now_datetime()
    date_only = dt.strftime("%Y%m%d")
    date_time = f"{date_only}120000"

    payload = {
        "supplierTin": doc.tax_id or "",
        "supplierBhfId": "",
        "supplierName": doc.supplier_name,
        "supplierInvcNo": "",
        "purchTypeCode": "N",
        "purchStatusCode": "02",
        "pmtTypeCode": "02",
        "purchDate": date_only,
        "occurredDate": date_only,
        "confirmDate": date_time,
        "warehouseDate": date_time,
        "remark": "MSKL",
        "mapping": doc.name,
        "itemsDataList": []
    }

    for item in doc.items:
        item_doc = frappe.get_doc("Item", item.item_code)
        tax_code = item_doc.custom_eTims_tax_code or ""
        if not tax_code:
            frappe.throw(
                msg=f"Item {item.item_name} does not have a valid eTims Tax Code. Please update the item before submitting the invoice.",
                title="eTims Error"
            )

        etims_log("Debug", "build_invoice_payload tax_code", tax_code)
        qty = abs(item.get("qty"))

        payload["itemsDataList"].append({
            "itemCode": item.item_code,
            "supplrItemClsCode": "",
            "supplrItemCode": "",
            "supplrItemName": "",
            "quantity": qty,
            "unitPrice": round(item.get("rate") or 0, 4),
            "taxTypeCode": tax_code,
            "pkgQuantity": qty,
            "discountRate": 0,
            "discountAmt": 0,
            "itemExprDate": ""
        })

    etims_log("Debug", "build_invoice_payload payload", frappe.as_json(payload))
    return payload




@frappe.whitelist()
def send_purchase_details(name: str) -> None:
    doc = frappe.get_doc("Purchase Invoice", name)
    submit_purchase_invoice(doc)

