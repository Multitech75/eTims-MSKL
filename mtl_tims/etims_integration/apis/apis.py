import asyncio
import json
from datetime import datetime
from io import BytesIO
import qrcode
# import aiohttp
import frappe
import frappe.defaults
from frappe.model.document import Document
from frappe.query_builder import DocType
import requests
from mtl_tims.etims_integration.logger import etims_log
from mtl_tims.etims_integration.utils import  get_settings

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

    # Build the payload
    payload = build_item_etims_payload(item)
    etims_log("Debug", "ETIMS Payload", payload)

    # Send request (synchronously or enqueue async)
    # response = send_item_to_etims(payload)
    # Enqueue send_item_to_etims instead of calling directly
    frappe.enqueue(
        send_item_to_etims,
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


def build_item_etims_payload(item) -> dict:
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
            "taxTypeCode": item.custom_eTims_tax_code, 
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
        "custom_eTims_tax_code",
    ]
    etims_log("Debug", "item registration item", item)
    return [field for field in required_fields if not item.get(field)]

def send_item_to_etims(payload: dict, item_name: str | None = None) -> dict:
    """Send the payload to eTims API (runs in background)"""
    try:
        settings_doc = get_settings()
        api_url = f"{settings_doc.get('etims_url', '').rstrip('/')}/AddItemsListV2?date=20200815125054"
        api_key = settings_doc.get("api_key")

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

        # Handle success
        if response_data.get("status") is True:
            if item_name:
                frappe.msgprint(
                    f"Item {item_name} registered in eTims. Message: {response_data.get('message')}"
                )

                # Update only if success
                if response_data.get("message") == "Success":
                    response_list = response_data.get("responseData") or []
                    if response_list and response_list[0].get("kraItemCode"):
                        frappe.db.set_value("Item", item_name, {
                            "custom_item_registered": 1,
                            "custom_item_eTims_message": response_data.get("message") or "",
                            "custom_item_code_etims": response_list[0]["kraItemCode"],
                            "custom_eTims_response": frappe.as_json(response_data) or "",
                        })
            
            return {
                "success": True,
                "message": response_data.get("message"),
                "data": response_data.get("responseData")
            }

        # Handle failure
        else:
            if item_name:
                response_list = response_data.get("responseData") or []
                detail_msg = response_data.get("message") or ""
                if response_list and response_list[0].get("message"):
                    detail_msg = f"{detail_msg} - {response_list[0].get('message')}"

                frappe.db.set_value("Item", item_name, {
                    "custom_item_eTims_message": detail_msg.strip(" -")
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
        error_message = str(e)
        cut_error_message = error_message.split("for url:")[0].strip() if "for url:" in error_message else error_message

        if item_name:
            frappe.db.set_value("Item", item_name, {
                "custom_item_eTims_message": cut_error_message,
                "custom_eTims_response": cut_error_message
            })

        etims_log("Error", f"API request failed: {error_message}")
        return {"success": False, "error": error_message}

# --------------------------------------------------------------------------------------#
#                            SALES INVOICE/PURCHASE SUBMISSION

@frappe.whitelist()
def send_payload_to_etims(payload: dict, api_url:str | None = None, api_key:str | None = None) -> dict:
    """Send payload to eTims and return response dict only."""
    # settings_doc = get_settings()
    # etims_log("Debug", "on_submit settings_doc", settings_doc)

    # if not settings_doc.get("api_key"):
    #     return
    # api_key = settings_doc.get("api_key")#"rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"
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


# --------------------------------------------------------------------------------------#
#                         SEND CUSTOMER/SUPPLIER DETAILS   
def send_to_etims(payload: dict,settings: dict | None, doc_name: str | None = None) -> dict:
    """Send the payload to eTims API (runs in background)"""
    try:
        api_url = f"{settings.get('etims_url', '').rstrip('/')}/AddCustomerV2"
        # api_url = settings.get("etims_url")#"http://41.139.135.45:8089/api/AddCustomerV2"
        api_key = settings.get("api_key")#"rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"

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

        # Handle success
        if response_data.get("status") is True:
            if doc_name:
                frappe.db.set_value("Customer", doc_name, {
                    "custom_details_submitted_successfully": 1,
                    "custom_eTims_message": response_data.get("message") or "",
                    "custom_eTims_response": frappe.as_json(response_data)
                })

            return {
                "success": True,
                "message": response_data.get("message"),
                "data": response_data.get("responseData")
            }

        #  Handle failure
        else:
            if doc_name:
                detail_msg = response_data.get("message") or "Unknown error"
                response_details = response_data.get("responseData") or {}
                update_values = {
                            "custom_eTims_message": detail_msg,
                            "custom_eTims_response": frappe.as_json(response_data)
                        }

                # If already registered, treat as success
                if detail_msg.strip().lower() == "customer already registered".lower():
                    update_values["custom_details_submitted_successfully"] = 1

                frappe.db.set_value("Customer", doc_name, update_values)

                frappe.msgprint(
                    msg=f"Failed to register Customer {doc_name} in eTims.\n{detail_msg}",
                    title="eTims Error",
                    indicator="red"
                )

            return {
                "success": False,
                "message": response_data.get("message"),
                "data": response_data.get("responseData")
            }

    except Exception as e:
        error_message = str(e)

        # If it's a requests error with "for url:", cut that part off
        if "for url:" in error_message:
            cut_error_message = error_message.split("for url:")[0].strip()
        
        update_values = {
            "custom_eTims_message": cut_error_message,
            "custom_eTims_response": cut_error_message
        }

        frappe.db.set_value("Customer", doc_name, update_values)
        etims_log("Error", f"API request failed: {error_message}")
        return {"success": False, "error": error_message}






# --------------------------------------------------------------------------------------#
#                            REGISTER CUSTOMER OR SUPPLIER

@frappe.whitelist()
def send_branch_customer_details(name: str, settings: dict | None, is_customer: bool = True) -> None:
    doctype = "Customer" if is_customer else "Supplier"
    data = frappe.get_doc(doctype, name)

    etims_log("Debug", "send_branch_customer_details doctype", doctype)
    etims_log("Debug", "send_branch_customer_details data", data)
    if (hasattr(data, 'disabled') and data.disabled) or (hasattr(data, 'custom_prevent_etims_registration') and data.custom_prevent_etims_registration):
        return
    
    payload = build_customer_etims_payload(data)
    etims_log("Debug", "ETIMS Payload", payload)
    
    # Send request (synchronously or enqueue async)
    # Enqueue send_to_etims instead of calling directly
    frappe.enqueue(
        send_to_etims,
        queue="default",
        is_async=True,
        job_name=f"Send {data.name} to eTims",
        payload=payload,
        doc_name=data.name,
        settings=settings
    )
    frappe.msgprint(
        f"{doctype} {data.name} passed validation and has been queued for eTims registration."
    )
    return {"success": True, "message": f"{doctype} {data.name} queued for eTims"}

  
def build_customer_etims_payload(data) -> dict:
    """Prepare payload for API call"""
    return {
            "customerNo": data.name, 
            "customerTin": data.tax_id or "", 
            "customerName": data.customer_name,
            "address": data.customer_primary_address or "",
            "telNo": data.customer_primary_contact or data.mobile_no or "", 
            "email": data.email_id or "", 
            "faxNo": "", 
            "isUsed": 1, 
            "remark": "MSKL" 
        }
    
