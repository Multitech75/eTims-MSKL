import asyncio
import json
from datetime import datetime
from io import BytesIO
import qrcode
import aiohttp
import frappe
import frappe.defaults
from frappe.model.document import Document
from frappe.query_builder import DocType
import requests
from mtl_tims.etims_integration.logger import etims_log

# --------------------------------------------------------------------------------------#
#                            ITEM REGISTRATION

@frappe.whitelist()
def perform_item_registration(item_name: str) -> dict | None:
    """Main function to handle item registration with eTims"""
    item = frappe.get_doc("Item", item_name)

    etims_log("Debug", "perform_item_registration item", item.item_name)
    if not is_item_eligible_for_registration(item):
        return None

    missing_fields = validate_required_fieldss(item)
    etims_log("Debug", "missing_fields item", missing_fields)
    if missing_fields:
        frappe.throw(
            title="Missing eTims Required Fields for " + item.item_name,
            msg="Please set the following fields:<br><br>" + "<br>".join(missing_fields)
        )


    etims_log("Debug", "validate_required_fields item", item)
    # Confirm if etims code is needed
    # if not item.custom_item_code_etims:
    #     generate_and_set_etims_code(item)
      # Build the payload
    payload = build_etims_payload(item)

    # Send request (synchronously or enqueue async)
    # response = send_to_etims(payload)
      # ✅ Enqueue send_to_etims instead of calling directly
    frappe.enqueue(
        send_to_etims,
        queue="default",
        is_async=True,
        job_name=f"Send {item.name} to eTims",
        payload=payload,
        item_name=item.name
    )
    frappe.msgprint(
        f"Item {item.name} passed validation and has been queued for eTims registration."
    )
    return {"success": True, "message": f"Item {item.name} queued for eTims"}


def build_etims_payload(item) -> dict:
    """Prepare payload for API call"""
    return [
        {
                "itemCode": item.item_code,
                "itemClassifiCode": item.custom_item_classification,  
                "itemTypeCode": "2",  # maybe map from your doc
                "itemName": item.item_name,
                "itemStrdName": item.item_name,
                "countryCode": item.custom_etims_country_of_origin or "KE",
                "pkgUnitCode": item.custom_packaging_unit,
                "qtyUnitCode": item.custom_unit_of_quantity,
                "taxTypeCode":  "B", 
                "batchNo": "",
                "barcode": "",
                "unitPrice": float(item.standard_rate or 0),
                "group1UnitPrice": 0,
                "group2UnitPrice": 0,
                "group3UnitPrice": 0,
                "group4UnitPrice": 0,
                "group5UnitPrice": 0,
                "additionalInfo": "",
                "saftyQuantity": 0,
                "isInrcApplicable": 1,
                "isUsed": 1,
                "quantity": 0,
        }
    ]


def send_to_etimss(payload: dict) -> dict:
    """Send the payload to eTims API"""
    try:
        api_url = "http://41.139.135.45:8089/api/AddItemsListV2?date=20200815125054"#frappe.db.get_single_value("eTims Settings", "api_url")
        api_key = "rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"#frappe.db.get_single_value("eTims Settings", "api_key")

        headers = {"key": f"{api_key}", "Content-Type": "application/json"}
        
        etims_log("Debug", f"Sending headers: {headers}")
        etims_log("Debug", f"Payload being sent: {frappe.as_json(payload)}")

        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        try:
            response_data = response.json()   # Try parse JSON
        except ValueError:
            response_data = response.text     # Fallback to raw text

        etims_log("Debug", f"Response status: {response.status_code}")
        etims_log("Debug", f"Response body: {frappe.as_json(response_data)}")
        
        # API-specific success/failure check
        if response_data.get("status") is True:
            return {"success": True, "message": response_data.get("message"), "data": response_data.get("responseData")}
        else:
            return {"success": False, "message": response_data.get("message"), "data": response_data.get("responseData")}
    except Exception as e:
        etims_log("Error", f"API request failed: {str(e)}")
        return {"success": False, "error": str(e)}


def is_item_eligible_for_registration(item) -> bool:
    """Check if item meets basic registration criteria"""
    return not (item.custom_prevent_etims_registration or item.disabled)

def validate_required_fieldss(item) -> list:
    """Validate required fields for item registration"""
    required_fields = [
        "custom_item_classification",
        "custom_etims_country_of_origin",
        "custom_item_classification_level",
        "custom_packaging_unit",
        "custom_unit_of_quantity",
        # "custom_taxation_type",
    ]
    etims_log("Debug", "item registration item", item)
    return [field for field in required_fields if not item.get(field)]

def send_to_etims(payload: dict, item_name: str | None = None) -> dict:
    """Send the payload to eTims API (runs in background)"""
    try:
        api_url = "http://41.139.135.45:8089/api/AddItemsListV2?date=20200815125054"
        api_key = "rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"

        headers = {"key": api_key, "Content-Type": "application/json"}
        
        etims_log("Debug", f"Sending headers: {headers}")
        etims_log("Debug", f"Payload being sent: {frappe.as_json(payload)}")

        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()

        try:
            response_data = response.json()
        except ValueError:
            response_data = {"status": False, "message": response.text, "responseData": []}

        etims_log("Debug", f"Response status: {response.status_code}")
        etims_log("Debug", f"Response body: {frappe.as_json(response_data)}")

        # ✅ Handle success
        if response_data.get("status") is True:
            if item_name:
                frappe.msgprint(
                    f"✅ Item {item_name} registered in eTims. Message: {response_data.get('message')}"
                )

                # Update KRA Code only if "Success"
                if response_data.get("message") == "Success":
                    response_list = response_data.get("responseData") or []
                    if response_list and response_list[0].get("kraItemCode"):
                        frappe.db.set_value("Item", item_name, {
                            "custom_item_registered": 1,
                            "custom_item_eTims_message": response_data.get("message") or "",
                            "custom_item_code_etims": response_list[0]["kraItemCode"]
                        })
            

            return {
                "success": True,
                "message": response_data.get("message"),
                "data": response_data.get("responseData")
            }

        #  Handle failure
        else:
            if item_name:
                response_list = response_data.get("responseData") or []
                if response_list and response_list[0].get("message") != "Success":
                    # Safely concatenate both messages
                    detail_msg = f"{response_data.get('message') or ''} - {response_list[0].get('message') or ''}".strip(" -")

                    frappe.db.set_value("Item", item_name, {
                        "custom_item_eTims_message": detail_msg
                    })
                frappe.msgprint(
                    msg=f"Failed to register item {item_name} in eTims.\n{response_data.get('message')}",
                    title="eTims Error",
                    indicator="red"
                )
            return {
                "success": False,
                "message": response_data.get("message"),
                "data": response_data.get("responseData")
            }

    except Exception as e:
        etims_log("Error", f"API request failed: {str(e)}")
        return {"success": False, "error": str(e)}


# --------------------------------------------------------------------------------------#
#                            SALES INVOICE SUBMISSION

@frappe.whitelist()
# def send_invoice_to_etims(payload: dict,invoice_name: str | None = None,invoice_type: str | None = None) -> dict:
#     """Send the payload to eTims API (runs in background)"""
#     try:
#         api_url = "http://41.139.135.45:8089/api/AddSaleV2"
#         api_key = "rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"

#         headers = {"key": api_key, "Content-Type": "application/json"}
        
#         etims_log("Debug", f"Sending headers: {headers}")
#         etims_log("Debug", f"Payload being sent: {frappe.as_json(payload)}")

#         response = requests.post(api_url, json=payload, headers=headers, timeout=30)
#         response.raise_for_status()

#         try:
#             response_data = response.json()
#         except ValueError:
#             response_data = {"status": False, "message": response.text, "responseData": []}

#         etims_log("Debug", f"Response status: {response.status_code}")
#         etims_log("Debug", f"Response body: {frappe.as_json(response_data)}")
#         if response_data.get("status") is True:
#             if invoice_name:
#                 frappe.msgprint(
#                     f"✅ Invoice {invoice_name} validated in eTims. Message: {response_data.get('message')}"
#                 )

#                 if response_data.get("message") == "Invoice Validated Successfully.":
#                     resp = response_data.get("responseData") or {}
#                     qr_url = resp.get("scuqrCode")
#                     image_url = generate_and_attach_qr_code(qr_url, invoice_name, invoice_type)

#                     # Convert sdcDateTime to MySQL datetime
#                     sdc_datetime = None
#                     if resp.get("sdcDateTime"):
#                         try:
#                             parsed_dt = datetime.strptime(resp["sdcDateTime"], "%Y%m%d%H%M%S")
#                             sdc_datetime = parsed_dt.strftime("%Y-%m-%d %H:%M:%S") 
#                         except Exception:
#                             etims_log("Error", f"Invalid sdcDateTime format: {resp['sdcDateTime']}")

#                         frappe.db.set_value("Sales Invoice", invoice_name, {
#                             "custom_successfully_submitted": 1,
#                             "custom_invoice_eTims_message": response_data.get("message"),
#                             "custom_current_receipt_number": str(resp.get("curRecptNo")),
#                             "custom_total_receipt_number": str(resp.get("totRecptNo")),
#                             "custom_control_unit_date_time": sdc_datetime,  
#                             "custom_scu_invoice_number": resp.get("invoiceNo"),
#                             "custom_receipt_signature": resp.get("scuReceiptSignature"),
#                             "custom_internal_data": resp.get("scuInternalData"),
#                             "custom_qr_code_url": qr_url,
#                             "custom_qr_code": image_url,
#                             "custom_eTims_response": frappe.as_json(response_data)
#                         })

#                     frappe.db.commit()   
#         #  Handle failure
#         else:
#             # If not validated, just throw error
#             frappe.throw(
#                 msg=f"Failed to validate invoice {invoice_name} in eTims.\n{response_data.get('message')}",
#                 title="eTims Error"
#             )
#             return

#     except Exception as e:
#         etims_log("Error", f"API request failed: {str(e)}")
#         return 

def send_invoice_to_etims(payload: dict, api_url:str | None = None) -> dict:
    """Send payload to eTims and return response dict only."""

    # api_url = "http://41.139.135.45:8089/api/AddSaleV2"
    api_key = "rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"
    headers = {"key": api_key, "Content-Type": "application/json"}

    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except ValueError:
        return {"status": False, "message": "Invalid JSON from eTims", "responseData": None}
    except Exception as e:
        etims_log("Error", f"API request failed: {str(e)}")
        return {"status": False, "message": str(e), "responseData": None}


def generate_and_attach_qr_code(url: str, docname: str, doctype: str) -> str:
    if not url:
        return None

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")

    # Always reset pointer
    buffer.seek(0)

    # Remove old QR if it exists
    existing_files = frappe.get_all(
        "File",
        filters={"attached_to_doctype": doctype, "attached_to_name": docname, "file_name": f"QR-{docname}.png"},
        pluck="name"
    )
    for f in existing_files:
        frappe.delete_doc("File", f, ignore_permissions=True, force=True)

    # Save new QR file
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": f"QR-{docname}.png",
        "is_private": 0,
        "attached_to_doctype": doctype,
        "attached_to_name": docname,
        "content": buffer.getvalue(),   # ✅ use getvalue()
    })
    file_doc.save(ignore_permissions=True)

    return file_doc.file_url



def generate_and_attach_qr_codes(url: str, docname: str, doctype: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": f"QR-{docname}.png",
        "is_private": 0,
        "content": buffer.read(),
        "attached_to_doctype": doctype,
        "attached_to_name": docname,
    })
    file_doc.save(ignore_permissions=True)

    return file_doc.file_url


def parse_datetime(date_str: str, format: str = "%Y-%m-%dT%H:%M:%S%z") -> str:
    if not date_str:
        return
    try:
        if "T" in date_str:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S%z")
        else:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
        return parsed_date.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
