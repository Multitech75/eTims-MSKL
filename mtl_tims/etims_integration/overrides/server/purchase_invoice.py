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

    if doc.is_return:
        submit_purchase_return(doc, settings_doc)
        return

    if not doc.is_return:
        payload = PURCHASE_PAYLOAD_BUILDERS[PURCHASE_API_VERSION](doc)
        api_url = f"{settings_doc.get('etims_url', '').rstrip('/')}/AddPurchase{PURCHASE_API_VERSION}"
        etims_log("Debug", "submit_purchase_invoice purchase api", PURCHASE_API_VERSION, api_url)
        api_key = settings_doc.get_password("api_key")#"rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"
        response = send_payload_to_etims(payload, api_url,api_key)

        etims_log("Debug", "generic_invoices_on_submit_override response", response)
        # check if response is Purchase already exist then set custom_submitted_successfully to 1
        
        # if not response.get("status"):
        if response.get("status") or "Purchase already exist" in (response.get("message")):
            doc.custom_submitted_successfully = 1
            doc.custom_purchase_invoice_eTims_message = response.get("message")
            doc.custom_eTims_response = frappe.as_json(response)
            etims_log(
                "Debug", "AddPurchaseV3 eTims invoiceNo", doc.name,
                ((response.get("responseData") or {}).get("invoiceNo")),
            )

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



def build_purchase_invoice_payload_v2(doc: Document) -> dict:
    """Payload for AddPurchaseV2 (original, kept for switching back)."""
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


def build_purchase_invoice_payload_v3(doc: Document) -> dict:
    """Payload for AddPurchaseV3."""
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
        "isStockUpdate": True,
        "itemsDataList": []
    }

    for item in doc.items:
        item_doc = frappe.get_doc("Item", item.item_code)
        missing = [
            label
            for label, val in (
                ("Packaging Unit", item_doc.get("custom_packaging_unit")),
                ("Unit of Quantity", item_doc.get("custom_unit_of_quantity")),
            )
            if not val
        ]
        if missing:
            etims_log("Error", "build_purchase_invoice_payload missing item fields", item.item_code, missing)
            frappe.throw(
                f"Item {item.item_name} is missing: {', '.join(missing)}. Cannot submit purchase invoice.",
                title="eTims Error",
            )

        tax_code = item_doc.custom_eTims_tax_code or ""
        if not tax_code:
            frappe.throw(
                msg=f"Item {item.item_name} does not have a valid eTims Tax Code. Please update the item before submitting the invoice.",
                title="eTims Error"
            )

        etims_log("Debug", "build_invoice_payload tax_code", tax_code)
        qty = abs(item.get("qty"))

        item_payload = {
            "itemCode": item.item_code,
            "itemName": item.item_name,
            "supplrItemClsCode": "",
            "supplrItemCode": "",
            "supplrItemName": "",
            "quantity": qty,
            "unitPrice": round(item.get("rate") or 0, 4),
            "taxTypeCode": tax_code,
            "pkgUnitCode": item_doc.get("custom_packaging_unit"),
            "pkgQuantity": qty,
            "qtyUnitCode": item_doc.get("custom_unit_of_quantity"),
            "discountRate": 0,
            "itemExprDt": "",
            "isItemStockUpdate": True,
            "isItemAlreadyStockIOUpdate": False,
        }
        etims_log("Debug", "build_purchase_invoice_payload item", item_payload)
        payload["itemsDataList"].append(item_payload)

    etims_log("Debug", "build_invoice_payload payload", frappe.as_json(payload))
    return payload




# Purchase submission API version: "V3" -> AddPurchaseV3, "V2" -> AddPurchaseV2 (switch back by changing this)
PURCHASE_API_VERSION = "V3"
PURCHASE_PAYLOAD_BUILDERS = {"V2": build_purchase_invoice_payload_v2, "V3": build_purchase_invoice_payload_v3}


@frappe.whitelist()
def send_purchase_details(name: str) -> None:
    doc = frappe.get_doc("Purchase Invoice", name)
    submit_purchase_invoice(doc, get_settings())


# --------------------------------------------------------------------------------------#
#                     PURCHASE RETURN / DEBIT NOTE (AddPurchaseReturnV3)

# eTims return reason codes: 01 Missing Qty, 02 Missing Item, 03 Damaged, 04 Wasted,
# 05 Raw Material Shortage, 06 Refund, 07 Wrong PIN, 08 Wrong Name, 09 Wrong Amount,
# 10 Wrong Qty, 11 Wrong Item(s), 12 Wrong Tax Type, 13 Other
DEFAULT_PURCHASE_RETURN_REASON = "06"


def submit_purchase_return(doc: Document, settings_doc: dict | None) -> dict:
    """Submit a Purchase Invoice return (debit note) to eTims via AddPurchaseReturnV3.

    Raises (blocking submission) if eTims rejects the return.
    """
    etims_log("Debug", "submit_purchase_return start", doc.name, "return_against:", doc.return_against)

    if not settings_doc:
        etims_log("Warning", "submit_purchase_return: no eTims settings found", doc.name)
        frappe.throw("eTims settings not found. Cannot submit purchase return.", title="eTims Error")

    if not doc.return_against:
        etims_log("Error", "submit_purchase_return: missing return_against", doc.name)
        frappe.throw(f"Debit Note {doc.name} has no original Purchase Invoice (Return Against).", title="eTims Error")

    original = frappe.get_doc("Purchase Invoice", doc.return_against)
    if not original.custom_submitted_successfully:
        etims_log("Error", "submit_purchase_return: original invoice not submitted to eTims", original.name)
        frappe.throw(
            f"Original Purchase Invoice {original.name} was not successfully submitted to eTims. Cannot process return.",
            title="eTims Error",
        )

    payload = build_purchase_return_payload(doc, original)

    base_url = (settings_doc.get("etims_url") or "").rstrip("/")
    api_key = settings_doc.get_password("api_key")
    if not base_url or not api_key:
        etims_log("Error", "submit_purchase_return: incomplete eTims settings (url/api_key)", doc.name)
        frappe.throw("eTims configuration is incomplete (URL / API key).", title="eTims Error")

    api_url = f"{base_url}/AddPurchaseReturnV3"
    etims_log("Debug", "submit_purchase_return api_url", api_url)
    etims_log("Debug", "submit_purchase_return payload", frappe.as_json(payload))

    response = send_payload_to_etims(payload, api_url, api_key) or {}
    etims_log("Debug", "submit_purchase_return response", doc.name, response)

    message = response.get("message") or ""
    if not response.get("status"):
        etims_log("Error", "submit_purchase_return failed", doc.name, message)
        frappe.throw(
            msg=f"Failed to submit purchase return {doc.name} to eTims.<br>{message}",
            title="eTims Error",
        )

    values = {
        "custom_submitted_successfully": 1,
        "custom_purchase_invoice_eTims_message": message,
        "custom_eTims_response": frappe.as_json(response),
    }
    if doc.docstatus == 1:
        # Already submitted (manual resend) -> persist directly
        doc.db_set(values, update_modified=False)
    else:
        doc.update(values)

    etims_log("Debug", "submit_purchase_return success", doc.name, message)
    return response


def _get_org_invoice_no(original: Document) -> int:
    """Original invoice number for eTims (orgInvoiceNo): the eTims `invoiceNo` returned
    by AddPurchaseV3 for the original Purchase Invoice (return_against), read from its
    stored custom_eTims_response, e.g. {"responseData": {"invoiceNo": "81"}} -> 81.
    """
    raw = original.get("custom_eTims_response")
    etims_log("Debug", "_get_org_invoice_no original", original.name, "stored response", raw)

    try:
        response = frappe.parse_json(raw) if raw else {}
    except Exception as e:
        etims_log("Error", "_get_org_invoice_no could not parse stored response", original.name, str(e))
        response = {}

    invoice_no = ((response or {}).get("responseData") or {}).get("invoiceNo")
    try:
        org_invoice_no = int(invoice_no)
    except (TypeError, ValueError):
        # e.g. originals submitted via AddPurchaseV2 (no invoiceNo returned) - don't block
        etims_log("Warning", "_get_org_invoice_no no valid eTims invoiceNo on original, sending 0",
                  original.name, invoice_no)
        return 0

    etims_log("Debug", "_get_org_invoice_no resolved", original.name, "->", org_invoice_no)
    return org_invoice_no


def build_purchase_return_payload(doc: Document, original: Document) -> dict:
    dt = now_datetime()
    date_only = dt.strftime("%Y%m%d")
    date_time = dt.strftime("%Y%m%d%H%M%S")
    purchase_date = frappe.utils.getdate(original.posting_date).strftime("%Y%m%d")

    payload = {
        "orgInvoiceNo": _get_org_invoice_no(original),
        "purchaseType": "N",
        "purchaseStatus": "02",
        "paymentType": "02",
        "returnDate": date_time,
        "confirmDate": date_time,
        "returnReason": DEFAULT_PURCHASE_RETURN_REASON,
        "purchaseDate": purchase_date,
        "stockReleseDate": date_time,
        "occurredDate": date_only,
        "remark": (doc.get("remarks") or "MSKL")[:400],
        "mapping": doc.name,
        "isStockUpdate": True,
        "itemsDataList": [],
    }

    for item in doc.items:
        item_doc = frappe.get_cached_doc("Item", item.item_code)
        qty = abs(item.get("qty") or 0)

        missing = [
            label
            for label, val in (
                ("eTims Item Code", item_doc.get("custom_item_code_etims")),
                ("Packaging Unit", item_doc.get("custom_packaging_unit")),
                ("Unit of Quantity", item_doc.get("custom_unit_of_quantity")),
            )
            if not val
        ]
        if missing:
            etims_log("Error", "build_purchase_return_payload missing item fields", item.item_code, missing)
            frappe.throw(
                f"Item {item.item_name} is missing: {', '.join(missing)}. Cannot submit purchase return.",
                title="eTims Error",
            )

        item_payload = {
            "itemCode": item.item_code,
            "itemName": item.item_name,
            "quantity": qty,
            "unitPrice": round(abs(item.get("rate") or 0), 4),
            "pkgUnitCode": item_doc.get("custom_packaging_unit"),
            "pkgQuantity": qty,
            "qtyUnitCode": item_doc.get("custom_unit_of_quantity"),
            "discountRate": round(item.get("discount_percentage") or 0, 2),
            "itemExprDt": "",
            "isItemStockUpdate": True,
        }
        etims_log("Debug", "build_purchase_return_payload item", item_payload)
        payload["itemsDataList"].append(item_payload)

    etims_log("Debug", "build_purchase_return_payload payload", frappe.as_json(payload))
    return payload


@frappe.whitelist()
def send_purchase_return_details(name: str) -> dict:
    """Manually (re)submit a Purchase Invoice return / debit note to eTims."""
    etims_log("Debug", "send_purchase_return_details called", name, "by", frappe.session.user)
    doc = frappe.get_doc("Purchase Invoice", name)
    doc.check_permission("submit")

    if not doc.is_return:
        frappe.throw(f"{name} is not a return / debit note.", title="eTims Error")
    if doc.custom_submitted_successfully:
        etims_log("Debug", "send_purchase_return_details already submitted", name)
        return {"status": True, "message": "Already submitted to eTims"}

    return submit_purchase_return(doc, get_settings())

