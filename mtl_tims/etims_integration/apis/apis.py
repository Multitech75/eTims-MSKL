import asyncio
import json
from datetime import datetime
from io import BytesIO
import qrcode
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
# def perform_item_registration(item_name: str) -> dict | None:
#     """Main function to handle item registration with eTims"""
#     item = frappe.get_doc("Item", item_name)

#     etims_log("Debug", "perform_item_registration item", item.item_name)
#     if not is_item_eligible_for_registration(item):
#         return None

#     missing_fields = validate_required_fields(item)
#     if missing_fields:
#         etims_log("Debug", "missing_fields item", missing_fields)
#         frappe.throw(
#             title="Missing eTims Required Fields for " + item.item_name,
#             msg="Please set the following fields:<br><br>" + "<br>".join(missing_fields)
#         )

#     #check if Item already registered mapped with BREAD-400G Item Code.
#     if item.custom_item_eTims_message == f"Item already mapped with {item.itemCode} Item Code."

#     # Build the payload for itemDetails
#     payload = build_item_etims_detail_payload(item)

#     # call api and save response
    


#     etims_log("Debug", "validate_required_fields item", item)

#     # Build the payload for registration
#     payload = build_item_etims_payload(item)
#     etims_log("Debug", "ETIMS Payload", payload)

#     # Send request (synchronously or enqueue async)
#     # response = send_item_to_etims(payload)
#     # Enqueue send_item_to_etims instead of calling directly
#     frappe.enqueue(
#         send_item_to_etims,
#         queue="default",
#         is_async=True,
#         job_name=f"Send {item.name} to eTims",
#         payload=payload,
#         item_name=item.name
#     )
#     frappe.msgprint(
#         f"Item {item.name} passed validation and has been queued for eTims registration."
#     )
#     return {"success": True, "message": f"Item {item.name} queued for eTims"}


def perform_item_registration(item_name: str) -> dict:
    """
    Main synchronous function to register an item or fetch its details from eTIMS.
    Guarantees the database is updated before returning control.
    """
    item = frappe.get_doc("Item", item_name)
    etims_log("Debug", f"Starting eTIMS processing for: {item.name}")

    if not is_item_eligible_for_registration(item):
        return {"success": False, "message": "Item not eligible for eTIMS."}

    missing_fields = validate_required_fields(item)
    if missing_fields:
        frappe.throw(
            title=f"Missing eTIMS Fields for {item.item_name}",
            msg="Please set the following fields:<br><br>" + "<br>".join(missing_fields)
        )

    # 1. FIRST CHECK: See if the item is already mapped/registered on eTIMS
    settings_doc = get_settings()
    api_key = settings_doc.get_password("api_key")
    base_url = settings_doc.get("etims_url", "").rstrip('/')
    headers = {"key": api_key, "Content-Type": "application/json"}

    detail_payload = build_item_etims_detail_payload(item)
    detail_url = f"{base_url}/ItemsDetailV2"

    try:
        etims_log("Debug", f"Checking item details via API: {detail_url}")
        detail_res = requests.post(detail_url, json=detail_payload, headers=headers, timeout=20)
        detail_res.raise_for_status()
        detail_data = detail_res.json()
        
        # If item already exists on eTIMS server, grab it and skip registration creation loop
        if detail_data.get("status") is True and detail_data.get("responseData"):
            kra_data = detail_data["responseData"][0]
            kra_item_code = kra_data.get("kraItemCode")
            
            if kra_item_code:
                frappe.db.set_value("Item", item.name, {
                    "custom_details_submitted_successfully": 1,
                    "custom_item_eTims_message": "Success (Fetched from Server)",
                    "custom_item_code_etims": kra_item_code,
                    "custom_eTims_response": frappe.as_json(detail_data)
                }, update_modified=False)
                
                # Commit explicitly to make it immediately visible to parent document validation reloads
                frappe.db.commit() 
                return {"success": True, "message": "Item details synchronized from eTIMS", "kraItemCode": kra_item_code}

    except Exception as e:
        etims_log("Warning", f"ItemsDetailV2 check failed, proceeding to direct registration: {str(e)}")

    # 2. REGISTRATION STEP: If it wasn't found via detail check, attempt registration creation
    reg_payload = build_item_etims_payload(item)
    reg_url = f"{base_url}/AddItemsListV2"

    try:
        etims_log("Debug", f"Sending direct registration payload to: {reg_url}")
        response = requests.post(reg_url, json=reg_payload, headers=headers, timeout=60)
        response.raise_for_status()
        response_data = response.json()
        
        etims_log("Debug", f"Registration response body: {frappe.as_json(response_data)}")

        # Handle Success Case
        if response_data.get("status") is True:
            response_list = response_data.get("responseData") or []
            if response_list and response_list[0].get("kraItemCode"):
                kra_item_code = response_list[0]["kraItemCode"]
                
                frappe.db.set_value("Item", item.name, {
                    "custom_details_submitted_successfully": 1,
                    "custom_item_eTims_message": response_data.get("message") or "Success",
                    "custom_item_code_etims": kra_item_code,
                    "custom_eTims_response": frappe.as_json(response_data),
                }, update_modified=False)
                
                frappe.db.commit()
                return {"success": True, "message": "Registered Successfully", "kraItemCode": kra_item_code}

        # Handle "Already Mapped" Error string gracefully
        error_msg = response_data.get("message") or ""
        if "already mapped" in error_msg.lower():
            # If the API blocks us because it's already mapped, mark database cleanly
            frappe.db.set_value("Item", item.name, {
                "custom_item_eTims_message": error_msg
            }, update_modified=False)
            frappe.db.commit()
            
            # Throw warning to user so they run sync or know they need to provide the kraItemCode manually
            frappe.throw(
                title="eTIMS Conflict",
                msg=f"eTIMS server states: <b>{error_msg}</b>. Please fetch details or resolve mappings."
            )

        # General Failure handling
        response_list = response_data.get("responseData") or []
        if response_list and response_list[0].get("message"):
            error_msg = f"{error_msg} - {response_list[0].get('message')}"

        frappe.db.set_value("Item", item.name, {
            "custom_item_eTims_message": error_msg.strip(" -")
        }, update_modified=False)
        frappe.db.commit()

        frappe.msgprint(
            msg=f"Failed to register item {item.name} in eTIMS.<br>{error_msg}",
            title="eTIMS Registration Error",
            indicator="red"
        )
        return {"success": False, "message": error_msg}

    except Exception as e:
        error_str = str(e)
        clean_error = error_str.split("for url:")[0].strip() if "for url:" in error_str else error_str

        frappe.db.set_value("Item", item.name, {
            "custom_item_eTims_message": clean_error,
            "custom_eTims_response": error_str
        }, update_modified=False)
        frappe.db.commit()
        
        etims_log("Error", f"API Request Connection Failed: {error_str}")
        return {"success": False, "error": clean_error}



def build_item_etims_detail_payload(item) -> dict:
    """Prepare payload for API call with strict string values"""
    
    # Safely extract item_code string whether item is an object or a dict
    item_code_val = item.item_code if hasattr(item, 'item_code') else item.get('item_code')
    
    return {
        "itemsCodeList": [
            {
                "itemCode": str(item_code_val).strip()
            }
        ]
    }



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

        response = requests.post(api_url, json=payload, headers=headers, timeout=60)
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

def validate_required_fields(item) -> list:
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

# def send_item_to_etims(payload: dict, item_name: str | None = None) -> dict:
#     """Send the payload to eTims API (runs in background)"""
#     try:
#         settings_doc = get_settings()
#         etims_log("Debug", f"settings_doc type: {type(settings_doc)}")
#         etims_log("Debug", f"has get_password: {hasattr(settings_doc, 'get_password')}")
        
#         api_url = f"{settings_doc.get('etims_url', '').rstrip('/')}/AddItemsListV2"#?date=20200815125054
#         # api_key = settings_doc.get("api_key")
#         api_key = settings_doc.get_password("api_key")
#         headers = {"key": api_key, "Content-Type": "application/json"}
        
#         # etims_log("Debug", f"Sending api_key: {api_key}")
#         # etims_log("Debug", f"Payload being sent: {frappe.as_json(payload)}")

#         response = requests.post(api_url, json=payload, headers=headers, timeout=60)
#         response.raise_for_status()

#         try:
#             response_data = response.json()
#         except ValueError:
#             response_data = {"status": False, "message": response.text, "responseData": []}

#         etims_log("Debug", f"Response status: {response.status_code}")
#         etims_log("Debug", f"Response body: {frappe.as_json(response_data)}")

#         # Handle success
#         if response_data.get("status") is True:
#             if item_name:
#                 frappe.msgprint(
#                     f"Item {item_name} registered in eTims. Message: {response_data.get('message')}"
#                 )

#                 # Update only if success
#                 if response_data.get("message") == "Success":
#                     response_list = response_data.get("responseData") or []
#                     if response_list and response_list[0].get("kraItemCode"):
#                         frappe.db.set_value("Item", item_name, {
#                             "custom_details_submitted_successfully": 1,
#                             "custom_item_eTims_message": response_data.get("message") or "",
#                             "custom_item_code_etims": response_list[0]["kraItemCode"],
#                             "custom_eTims_response": frappe.as_json(response_data) or "",
#                         })
            
#             return {
#                 "success": True,
#                 "message": response_data.get("message"),
#                 "data": response_data.get("responseData")
#             }

#         # Handle failure
#         else:
#             if item_name:
#                 response_list = response_data.get("responseData") or []
#                 detail_msg = response_data.get("message") or ""
#                 if response_list and response_list[0].get("message"):
#                     detail_msg = f"{detail_msg} - {response_list[0].get('message')}"

#                 frappe.db.set_value("Item", item_name, {
#                     "custom_item_eTims_message": detail_msg.strip(" -")
#                 })

#                 frappe.msgprint(
#                     msg=f"Failed to register item {item_name} in eTims.\n{response_data.get('message')}",
#                     title="eTims Error",
#                     indicator="red"
#                 )

#             return {
#                 "success": False,
#                 "message": response_data.get("message"),
#                 "data": response_data.get("responseData")
#             }

#     except Exception as e:
#         error_message = str(e)
#         cut_error_message = error_message.split("for url:")[0].strip() if "for url:" in error_message else error_message

#         if item_name:
#             frappe.db.set_value("Item", item_name, {
#                 "custom_item_eTims_message": cut_error_message,
#                 "custom_eTims_response": cut_error_message
#             })

#         etims_log("Error", f"API request failed: {error_message}")
#         return {"success": False, "error": error_message}








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
        response = requests.post(api_url, json=payload, headers=headers, timeout=60)
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
        # api_key = settings.get("api_key")#"rVrIW7Yt+h1zB2MUNDJUbQlwqBcaP1vIKK1FDyfe16IF14If/q1vp2qdAVChDa66"
        api_key = settings_doc.get_password("api_key")
        headers = {"key": api_key, "Content-Type": "application/json"}
        
        etims_log("Debug", f"Sending headers: {headers}")
        etims_log("Debug", f"Payload being sent: {frappe.as_json(payload)}")

        response = requests.post(api_url, json=payload, headers=headers, timeout=60)
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
            "customerNo": (data.name[9:] if len(data.name) > 9 else data.name),  # Max 9 chars
            "customerTin": data.tax_id or "", 
            "customerName": data.customer_name,
            "address": data.customer_primary_address or "",
            "telNo": data.customer_primary_contact or data.mobile_no or "", 
            "email": data.email_id or "", 
            "faxNo": "", 
            "isUsed": 1, 
            "remark": "MSKL" 
        }
    





# --------------------------------------------------------------------------------------#
#                            BULK ITEM REGISTRATION

# @frappe.whitelist()
# def register_all_items(doctype:str = None) -> None:
#     settings = get_settings()
#     if not settings:
#         return
#     submit_all("Item")
#     etims_log("Debug", "register_all_items settings", settings)
    # Item = DocType("Item")
    # etims_log("Debug", "register_all_items Item", Item)
    # items = (
    #     frappe.qb.from_(Item)
    #     .select(Item.name)
    #     .where(
    #         (Item.custom_details_submitted_successfully != 1) &
    #         (Item.disabled != 1) 
    #     )
    #     .run(as_dict=True)
    # )
    
    # etims_log("Debug", "register_all_items Item", Item)
    # for item in items:
    #     perform_item_registration(item.name)



@frappe.whitelist()
def submit_all(doctype: str = None) -> None:
    settings = get_settings()
    if not settings:
        return

    Doc = DocType(doctype)
    etims_log("Debug", "submit_all doctype", doctype)
    etims_log("Debug", "submit_all Doc", Doc)

    items = (
        frappe.qb.from_(Doc)
        .select(Doc.name)
        .where(
            (Doc.custom_details_submitted_successfully != 1)
            & (Doc.disabled != 1)
        )
        .run(as_dict=True)
    )

    etims_log("Debug", "submit_all items", items)

    for item in items:
        if doctype == "Item":
            perform_item_registration(item["name"])
        else:
            send_branch_customer_details(item["name"], settings, True)

# --------------------------------------------------------------------------------------#
#                            BULK CUSTOMER REGISTRATION

@frappe.whitelist()
def bulk_submit_customers(docs_list: str) -> None:
    customers = json.loads(docs_list)
    settings = get_settings()
    if not settings:
        return
    etims_log("Debug", "bulk_submit_customers customers", customers)
    for customer in customers:
        send_branch_customer_details(customer, settings, True)