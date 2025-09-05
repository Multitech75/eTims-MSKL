"""Utility functions"""

import json
import re
import secrets
import string
from base64 import b64encode
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from io import BytesIO
from typing import Any, Dict, List, Union
from urllib.parse import urlencode

import aiohttp
import qrcode
import requests
from aiohttp import ClientTimeout

import frappe
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import now_datetime

from .doctype.doctype_names_mapping import (
    ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME,
    ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
    ORGANISATION_MAPPING_DOCTYPE_NAME,
    PACKAGING_UNIT_DOCTYPE_NAME,
    ROUTES_TABLE_CHILD_DOCTYPE_NAME,
    ROUTES_TABLE_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
    SLADE_ID_MAPPING_DOCTYPE_NAME,
    TAXATION_TYPE_DOCTYPE_NAME,
    UNIT_OF_QUANTITY_DOCTYPE_NAME,
    WORKSTATION_DOCTYPE_NAME,
)
from .logger import etims_log


def is_valid_kra_pin(pin: str) -> bool:
    """Checks if the string provided conforms to the pattern of a KRA PIN.
    This function does not validate if the PIN actually exists, only that
    it resembles a valid KRA PIN.

    Args:
        pin (str): The KRA PIN to test

    Returns:
        bool: True if input is a valid KRA PIN, False otherwise
    """
    pattern = r"^[a-zA-Z]{1}[0-9]{9}[a-zA-Z]{1}$"
    return bool(re.match(pattern, pin))


async def make_get_request(url: str) -> dict[str, str] | str:
    """Make an Asynchronous GET Request to specified URL

    Args:
        url (str): The URL

    Returns:
        dict: The Response
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.content_type.startswith("text"):
                return await response.text()

            return await response.json()


async def make_post_request(
    url: str,
    data: dict[str, str] | None = None,
    headers: dict[str, str | int] | None = None,
) -> dict[str, str | dict]:
    """Make an Asynchronous POST Request to specified URL

    Args:
        url (str): The URL
        data (dict[str, str] | None, optional): Data to send to server. Defaults to None.
        headers (dict[str, str | int] | None, optional): Headers to set. Defaults to None.

    Returns:
        dict: The Server Response
    """
    # TODO: Refactor to a more efficient handling of creation of the session object
    # as described in documentation
    async with aiohttp.ClientSession(timeout=ClientTimeout(1800)) as session:
        # Timeout of 1800 or 30 mins, especially for fetching Item classification
        async with session.post(url, json=data, headers=headers) as response:
            return await response.json()


def build_datetime_from_string(
    date_string: str, format: str = "%Y-%m-%d %H:%M:%S"
) -> datetime:
    """Builds a Datetime object from string, and format provided

    Args:
        date_string (str): The string to build object from
        format (str, optional): The format of the date_string string. Defaults to "%Y-%m-%d".

    Returns:
        datetime: The datetime object
    """
    date_object = datetime.strptime(date_string, format)

    return date_object


def is_valid_url(url: str) -> bool:
    """Validates input is a valid URL

    Args:
        input (str): The input to validate

    Returns:
        bool: Validation result
    """
    pattern = r"^(https?|ftp):\/\/[^\s/$.?#].[^\s]*"
    return bool(re.match(pattern, url))


def get_route_path(
    search_field: str,
    vendor: str = "OSCU KRA",
    routes_table_doctype: str = ROUTES_TABLE_CHILD_DOCTYPE_NAME,
    parent_doctype: str = ROUTES_TABLE_DOCTYPE_NAME,
) -> tuple[str, str] | None:

    RoutesTable = DocType(routes_table_doctype)
    ParentTable = DocType(parent_doctype)

    query = (
        frappe.qb.from_(RoutesTable)
        .join(ParentTable)
        .on(RoutesTable.parent == ParentTable.name)
        .select(RoutesTable.url_path, RoutesTable.last_request_date)
        .where(
            (RoutesTable.url_path_function.like(search_field))
            & (ParentTable.vendor.like(vendor))
        )
        .limit(1)
    )

    results = query.run(as_dict=True)

    if results:
        return (results[0]["url_path"], results[0]["last_request_date"])

    return None, None


def get_environment_settings(
    company_name: str,
    vendor: str,
    doctype: str = SETTINGS_DOCTYPE_NAME,
    environment: str = "Sandbox",
    branch_id: str = "00",
) -> Document | None:
    error_message = None

    Settings = DocType(doctype)

    query = (
        frappe.qb.from_(Settings)
        .select(
            Settings.server_url,
            Settings.name,
            Settings.vendor,
            Settings.tin,
            Settings.dvcsrlno,
            Settings.bhfid,
            Settings.company,
            Settings.communication_key,
            Settings.sales_control_unit_id.as_("scu_id"),
        )
        .where(
            (Settings.company == company_name)
            & (Settings.env == environment)
            & (Settings.vendor == vendor)
            & (Settings.is_active == 1)
        )
    )

    if branch_id:
        query = query.where(Settings.bhfid == branch_id)

    setting_doctype = query.run(as_dict=True)

    if setting_doctype:
        return setting_doctype[0]

    error_message = f"""
        There is no valid environment setting for these credentials:
            <ul>
                <li>Company: <b>{company_name}</b></li>
                <li>Branch ID: <b>{branch_id}</b></li>
                <li>Environment: <b>{environment}</b></li>
            </ul>
        Please ensure a valid <a href="/app/navari-kra-etims-settings">eTims Integration Setting</a> record exists
    """

    etims_logger.error(error_message)
    frappe.log_error(
        title="Incorrect Setup", message=error_message, reference_doctype=doctype
    )
    frappe.throw(error_message, title="Incorrect Setup")


def get_current_environment_state(
    environment_identifier_doctype: str = ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME,
) -> str:
    """Fetches the Environment Identifier from the relevant doctype.

    Args:
        environment_identifier_doctype (str, optional): The doctype containing environment information. Defaults to ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME.

    Returns:
        str: The environment identifier. Either "Sandbox", or "Production"
    """
    environment = frappe.db.get_single_value(
        environment_identifier_doctype, "environment"
    )

    return environment


def get_server_url(company_name: str, branch_id: str = "00", settings_name: str = None) -> str | None:
    settings = get_settings(company_name, branch_id, settings_name)

    if settings:
        server_url = settings.get("server_url")

        return server_url

    return


def build_headers(company_name: str, branch_id: str, settings_name: str = None) -> dict[str, str] | None:
    """
    Build headers for Slade360 API requests.
    Checks for token validity and refreshes the token if expired.

    Args:
        company_name (str): The name of the company.
        branch_id (str, optional): The branch ID. Defaults to "00".

    Returns:
        dict[str, str] | None: The headers including the refreshed token or None if failed.
    """
    settings = get_settings(company_name, branch_id, settings_name)

    if settings:
        access_token = settings.get("access_token")
        token_expiry = settings.get("token_expiry")

        if (
            not access_token
            or not token_expiry
            or (
                datetime.strptime(str(token_expiry).split(".")[0], "%Y-%m-%d %H:%M:%S")
                < datetime.now()
            )
        ):
            new_settings = update_navari_settings_with_token(settings.get("name"))

            if not new_settings:
                frappe.throw(
                    "Failed to refresh token. Please check your Slade360 integration settings.",
                    frappe.AuthenticationError,
                )

            access_token = new_settings.access_token

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        workstation = None
        if company_name:
            mapping = next((m for m in settings.get("organisation_mapping", []) 
                  if m.get("company") == company_name), None)
            if mapping:
                workstation = frappe.db.get_value(WORKSTATION_DOCTYPE_NAME, {"name": mapping.get("workstation")}, "slade_id")

        if workstation: 
            headers["X-Workstation"] = workstation

        return headers

    return None











def get_settings(company_name: str = None, branch_id: str = None, settings_name: str = None) -> dict | None:
    """Fetch settings for a given company and branch.

    Args:
        company_name (str, optional): The name of the company. Defaults to None.
        branch_id (str, optional): The branch ID. Defaults to None.

    Returns:
        dict | None: The settings if found, otherwise None.
    """
    if settings_name:
        if frappe.db.exists(SETTINGS_DOCTYPE_NAME, {"name": settings_name}):
            return frappe.get_doc(SETTINGS_DOCTYPE_NAME, settings_name).as_dict()
        
    company_name = (
        company_name
        or frappe.defaults.get_user_default("Company")
        or frappe.get_value("Company", {}, "name")
    )
    if frappe.db.exists(
        ORGANISATION_MAPPING_DOCTYPE_NAME, 
        {"company": company_name, "is_active": 1}
    ):
        mapping = frappe.db.get_value(
            ORGANISATION_MAPPING_DOCTYPE_NAME,
            {"company": company_name, "is_active": 1},
            "parent",
            as_dict=True,
        )
        if mapping and mapping.parent:
            return frappe.get_doc(SETTINGS_DOCTYPE_NAME, mapping.parent).as_dict()
    
    if frappe.db.exists(SETTINGS_DOCTYPE_NAME, {"is_active": 1}):
        settings = frappe.db.get_value(
            SETTINGS_DOCTYPE_NAME,
            {"is_active": 1},
            "*",
            as_dict=True,
        )
        return settings
    
    return None


        

@frappe.whitelist()
def get_active_settings(doctype: str = SETTINGS_DOCTYPE_NAME) -> list[dict]:
    try:
        results = frappe.get_all(
            doctype,
            filters={"is_active": 1},
            fields=["name", "company"],
            ignore_permissions=True  
        )
        return results
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), _("Failed to get active settings"))
        frappe.throw(_("An error occurred while fetching settings"))



























def get_branch_id(company_name: str, vendor: str) -> str | None:
    settings = get_curr_env_etims_settings(company_name, vendor)

    if settings:
        return settings.bhfid

    return None


def extract_document_series_number(document: Document) -> int | None:
    split_invoice_name = document.name.split("-")

    if len(split_invoice_name) == 4:
        return int(split_invoice_name[-1])

    if len(split_invoice_name) == 5:
        return int(split_invoice_name[-2])




def get_invoice_items_list(invoice: Document) -> list[dict[str, str | int | None]]:
    """Iterates over the invoice items and extracts relevant data

    Args:
        invoice (Document): The invoice

    Returns:
        list[dict[str, str | int | None]]: The parsed data as a list of dictionaries
    """
    # FIXME: Handle cases where same item can appear on different lines with different rates etc.
    # item_taxes = get_itemised_tax_breakup_data(invoice)
    items_list = []

    for index, item in enumerate(invoice.items):
        # taxable_amount = round(int(item_taxes[index]["taxable_amount"]), 2)
        # actual_tax_amount = 0
        # tax_head = invoice.taxes[0].description  # Fetch tax head from taxes table

        # actual_tax_amount = item_taxes[index][tax_head]["tax_amount"]

        # tax_amount = round(actual_tax_amount, 2)

        items_list.append(
            {
                "product": item.item_code,
                "quantity": abs(item.qty),
            }
        )

    return items_list


def update_last_request_date(
    response_datetime: str,
    route: str,
    routes_table: str = ROUTES_TABLE_CHILD_DOCTYPE_NAME,
) -> None:
    if len(route) < 5:
        return

    doc = frappe.get_doc(
        routes_table,
        {"url_path": route}
    )

    doc.last_request_date = response_datetime

    doc.save(ignore_permissions=True)
    frappe.db.commit()


def get_curr_env_etims_settings(
    company_name: str, vendor: str, branch_id: str = "00"
) -> Document | None:
    current_environment = get_current_environment_state(
        ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME
    )
    settings = get_environment_settings(
        company_name, vendor, environment=current_environment, branch_id=branch_id
    )

    if settings:
        return settings


def get_most_recent_sales_number(
    company_name: str, vendor: str = "OSCU KRA"
) -> int | None:
    settings = get_curr_env_etims_settings(company_name, vendor)

    if settings:
        return settings.most_recent_sales_number

    return


def get_qr_code(data: str) -> str:
    """Generate QR Code data

    Args:
        data (str): The information used to generate the QR Code

    Returns:
        str: The QR Code.
    """
    qr_code_bytes = get_qr_code_bytes(data, format="PNG")
    base_64_string = bytes_to_base64_string(qr_code_bytes)

    return add_file_info(base_64_string)


def add_file_info(data: str) -> str:
    """Add info about the file type and encoding.

    This is required so the browser can make sense of the data."""
    return f"data:image/png;base64, {data}"


def get_qr_code_bytes(data: bytes | str, format: str = "PNG") -> bytes:
    """Create a QR code and return the bytes."""
    img = qrcode.make(data)

    buffered = BytesIO()
    img.save(buffered, format=format)

    return buffered.getvalue()


def bytes_to_base64_string(data: bytes) -> str:
    """Convert bytes to a base64 encoded string."""
    return b64encode(data).decode("utf-8")


def quantize_number(number: str | int | float) -> str:
    """Return number value to two decimal points"""
    return Decimal(number).quantize(Decimal(".01"), rounding=ROUND_DOWN).to_eng_string()


def split_user_email(email_string: str) -> str:
    """Retrieve portion before @ from an email string"""
    return email_string.split("@")[0]










"""
    START OF SALES INVOICE PAYLOAD BUILDING AND TAX CALCULATION
"""
def build_invoice_payload(
    invoice: Document
) -> dict:
    reference_number = get_invoice_reference_number(invoice)
    # get current datetime (server time)
    dt = now_datetime()
    dateOnly = dt.strftime("%Y%m%d")
    dateTime = f"{dateOnly}120000"
    payload = {
        "customerNo": frappe.get_value("Customer", invoice.customer, "customer_name") or None,
        "customerName": frappe.get_value("Customer", invoice.customer, "customer_name") or None,
        "customerMobileNo": "",
        "salesType": "N",
        "paymentType": "02", #01- CASH, 02- CREDIT
        "traderInvoiceNo": invoice.name,
        "confirmDate": dateTime,
        "salesDate": dateOnly,
        "stockReleseDate": dateTime,
        "receiptPublishDate": dateTime,
        "occurredDate": dateOnly,
        "invoiceStatusCode": "02",
        "remark": "MSKL",
        "isPurchaseAccept": 1,
        "mapping": invoice.name,
        "saleItemList": []
    }
    
    etims_log("Debug", "build_invoice_payload payload reference_number", reference_number,payload)
    calculate_tax(invoice)
    
    for item in invoice.items:
        tax_amount = item.get("custom_tax_amount", 0) or 0
        qty = abs(item.get("qty"))
        base_net_rate = round(item.get("base_net_rate") or 0, 4)
        tax_code = item.get("taxation_type_code", "A") or "A"
        etims_log("Debug", "build_invoice_payload tax_code item", tax_code,item)
        payload["saleItemList"].append({
            "itemCode": item.item_code,
            "taxTypeCode": tax_code,
            "unitPrice": round(base_net_rate + (tax_amount / qty if qty else 0), 4),
            "pkgQuantity": qty,
            "quantity": qty,
            # "uom": item.uom or "Pcs",
            "discountRate": 0,
            "discountAmt": 0
        })

    # etims_log("Debug", "build_invoice_payload payload", payload)
    return payload


    """ END OF SALES INVOICE PAYLOAD BUILDING AND TAX CALCULATION"""




"""
    START OF SALES CREDITNOTE PAYLOAD BUILDING AND TAX CALCULATION
"""

def build_creditnote_payload(
    invoice: Document, reference_number: str = None
) -> dict:
    # get current datetime (server time)
    dt = now_datetime()
    dateOnly = dt.strftime("%Y%m%d")
    dateTime = f"{dateOnly}120000"
    payload = {
        "orgInvoiceNo": reference_number,
        "traderInvoiceNo": invoice.name,
        "salesType": "N",
        "paymentType": "01", #01- CASH, 02- CREDIT
        "creditNoteDate": dateTime,
        "confirmDate": dateTime,
        "salesDate": dateOnly,
        "stockReleseDate": dateTime,
        "receiptPublishDate": dateTime,
        "occurredDate": dateOnly,
        "creditNoteReason": "06",
        "invoiceStatusCode": "02",
        "isPurchaseAccept": 1,
        "remark": "MSKL",
        "mapping": invoice.name,
        "creditNoteItemsList": []
    }
    
    etims_log("Debug", "build_creditnote_payload payload reference_number", reference_number,payload)
    calculate_tax(invoice)
    
    for item in invoice.items:
        tax_amount = item.get("custom_tax_amount", 0) or 0
        qty = abs(item.get("qty"))
        base_net_rate = round(item.get("base_net_rate") or 0, 4)
        tax_code = item.get("taxation_type_code", "A") or "A"
        etims_log("Debug", "build_creditnote_payload tax_code item", tax_code,item)
        payload["creditNoteItemsList"].append({
            "itemCode": item.item_code,
            "unitPrice": round(base_net_rate + (tax_amount / qty if qty else 0), 4),
            "quantity": qty,
            "discountRate": 0,
        })

    # etims_log("Debug", "build_invoice_payload payload", payload)
    return payload


    """ END OF SALES CREDITNOTE PAYLOAD BUILDING AND TAX CALCULATION"""




    """ START OF ITEM TAX CALCULATION"""




    """ START OF ITEM TAX CALCULATION"""
def before_save_(doc: "Document", method: str | None = None) -> None:
    #checks if eTims is set to active
    # if not frappe.db.exists(SETTINGS_DOCTYPE_NAME, {"is_active": 1}): 
    #     return
    
    # etims_logger.error(doc)
    etims_log("Debug", "before_save_", doc)

    calculate_tax(doc) 


def calculate_tax(doc: "Document") -> None:
    """
    Calculate tax for each item in the document using either:
    - Item-level tax templates (if any item has one), or
    - Document-level taxes (if no items have tax templates)
    Then set taxation type codes for all items.
    """
    taxes = doc.get("taxes", [])
    has_item_level_tax = any(item.item_tax_template for item in doc.items)
    
    etims_log("Debug", "has_item_level_tax", has_item_level_tax)
    if has_item_level_tax:
        _calculate_item_level_taxes(doc)
    elif taxes:
        _calculate_document_level_taxes(doc, taxes)
    
    _set_taxation_type_codes(doc)



def _calculate_item_level_taxes(doc: "Document") -> None:
    """Calculate taxes using each item's individual tax template"""
    for item in doc.items:
        tax_rate = get_item_tax_rate(item.item_tax_template) if item.item_tax_template else None
        tax_amount = item.base_net_amount * tax_rate / 100 if tax_rate else 0
        
        etims_log("Debug", "_calculate_item_level_taxes", tax_amount)
        item.custom_tax_amount = tax_amount
        item.custom_tax_rate = tax_rate if tax_rate else 0


def _calculate_document_level_taxes(doc: "Document", taxes: list) -> None:
    """
    Distribute document-level taxes proportionally across all items.
    Tax rates are calculated from the distributed tax amount and item net amount.
    """
    total_net_amount = sum(item.base_net_amount for item in doc.items)
    if total_net_amount == 0:
        return
    
    total_tax_amount = sum(tax.tax_amount for tax in taxes)
    
    etims_log("Debug", "_calculate_document_level_taxes", total_tax_amount)
    for item in doc.items:
        item_ratio = item.base_net_amount / total_net_amount
        item.custom_tax_amount = total_tax_amount * item_ratio
        
        if item.base_net_amount > 0:
            item.custom_tax_rate = (item.custom_tax_amount / item.base_net_amount) * 100
        else:
            item.custom_tax_rate = 0


def get_item_tax_rate(item_tax_template: str) -> float:
    """Return the sum of all tax rates in the given Item Tax Template"""
    tax_template = frappe.get_doc("Item Tax Template", item_tax_template)
    etims_log("Debug", "_calculaget_item_tax_ratete_item_level_taxes", tax_template)
    return sum(tax.tax_rate for tax in tax_template.taxes) if tax_template.taxes else 0


def _set_taxation_type_codes(doc: "Document") -> None:
    """
    Determine taxation type code for each item using this priority:
    1. From item's tax template (if exists)
    2. From item master data
    3. Based on tax rate (B for ≥16%, E for ≥8%, A for 0%)
    4. Default to B if none of the above apply
    """
    etims_log("Debug", "_set_taxation_type_codes doc", doc)
    for item in doc.items:
        # Load the linked Item
        item_doc = frappe.get_doc("Item", item.item_code)
        etims_log("Debug", "_set_taxation_type_codes item_doc", item_doc.name)

        # Ensure item is eTims registered
        if not item_doc.custom_item_code_etims:
            from .apis.apis import perform_item_registration
            perform_item_registration(item_doc.name)
        
        item.taxation_type_code = (
            _get_taxation_type_from_template(item) or
            _get_taxation_type_from_rate(item) or
            # _get_taxation_type_from_item(item) or
            "A" # fallback default
        )
        # item_doc = frappe.get_doc("Item",item.item_code )
        # etims_log("Debug", "_set_taxation_type_codes item.taxation_type_code", item.taxation_type_code,item_doc)

        # # Item tax template (if set)
        # tax_template = item_doc.get("taxes")
        # etims_log("Debug", "_set_taxation_type_codes taxes", tax_template)
        # if tax_template:
        #     for tax in tax_template:
        #         etims_log("Debug", "_set_taxation_type_codes taxes", tax.tax_type, tax.tax_rate)
        #         print("Account Head:", tax.tax_type, " | Tax Rate:", tax.tax_rate)


def _get_taxation_type_from_item(item) -> str:
    """Get taxation type from item master data if available"""
    return frappe.get_value("Item", item.item_code, "custom_taxation_type") or ""

def _get_taxation_type_from_template(item) -> str:
    """Get taxation type from item's tax template if available"""
    etims_log("Debug", "_get_taxation_type_from_template", item)
    if item.item_tax_template:
        return frappe.get_value("Item Tax Template", item.item_tax_template, "custom_etims_taxation_type")
    return ""


def _get_taxation_type_from_rate(item) -> str:
    """Determine taxation type based on item's tax rate"""
    if not hasattr(item, 'custom_tax_rate'):
        return ""
    if round(item.custom_tax_rate) >= 16:
        return "B"
    elif round(item.custom_tax_rate) >= 8:
        return "E"
    elif item.custom_tax_rate == 0:
        return "A"
    return ""




    """ END OF ITEM TAX CALCULATION"""


















"""Uncomment this function if you need document-level tax rate calculation in the future
A classic example usecase is Apex tevin typecase where the tax rate is fetched from the document's Sales Taxes and Charges Template
"""
# def get_doc_tax_rate(doc_tax_template: str) -> float | None:
#     """Fetch the tax rate from the document's Sales Taxes and Charges Template."""
#     tax_template = frappe.get_doc("Sales Taxes and Charges Template", doc_tax_template)
#     if tax_template.taxes:
#         return tax_template.taxes[0].rate
#     return None


def parse_request_data(request_data: str | dict) -> dict:
    if isinstance(request_data, str):
        return json.loads(request_data)
    elif isinstance(request_data, (dict, list)):
        return request_data
    return {}


def get_invoice_number(invoice_name: str) -> int:
    """
    Extracts the numeric portion from the invoice naming series.

    Args:
        invoice_name (str): The name of the Sales Invoice document (e.g., 'eTIMS-INV-00-00001').

    Returns:
        int: The extracted invoice number.
    """
    parts = invoice_name.split("-")
    if len(parts) >= 3:
        return int(parts[-1])
    else:
        raise ValueError("Invoice name format is incorrect")


"""For cancelled and amended invoices"""


def clean_invc_no(invoice_name: str) -> str:
    if "-" in invoice_name:
        invoice_name = "-".join(invoice_name.split("-")[:-1])
    return invoice_name


def get_taxation_types(doc: dict) -> dict:
    taxation_totals = {}

    # Loop through each item in the Sales Invoice
    for item in doc.items:
        # Fetch the taxation type using item_code
        taxation_type = frappe.db.get_value(
            "Item", item.item_code, "custom_taxation_type"
        )
        taxable_amount = item.net_amount
        tax_amount = item.custom_tax_amount

        # Fetch the tax rate for the current taxation type from the specified doctype
        tax_rate = frappe.db.get_value(
            "Navari KRA eTims Taxation Type", taxation_type, "userdfncd1"
        )
        # If the taxation type already exists in the dictionary, update the totals
        if taxation_type in taxation_totals:
            taxation_totals[taxation_type]["taxable_amount"] += taxable_amount
            taxation_totals[taxation_type]["tax_amount"] += tax_amount

        else:
            taxation_totals[taxation_type] = {
                "tax_rate": tax_rate,
                "tax_amount": tax_amount,
                "taxable_amount": taxable_amount,
            }

    return taxation_totals


def authenticate_and_get_token(
    auth_server_url: str,
    username: str,
    password: str,
    client_id: str,
    client_secret: str,
    docname: str = None,
) -> dict:
    url = f"{auth_server_url}/oauth2/token/"
    payload = {
        "username": username,
        "password": password,
        "grant_type": "password",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    integration_request = create_request_log(
        data=json.dumps(payload),
        request_description="Slade360 eTims Authentication",
        is_remote_request=True,
        service_name="Slade360 eTims Authentication",
        request_headers=json.dumps(headers),
        url=url,
        reference_doctype=SETTINGS_DOCTYPE_NAME,
        reference_docname=docname,
    )

    try:
        response = requests.post(url, headers=headers, data=urlencode(payload))
        frappe.db.set_value("Integration Request", integration_request.name, "output", response.text, update_modified=False)

        if response.ok:
            data = response.json()
            frappe.db.set_value("Integration Request", integration_request.name, "status", "Completed", update_modified=False)
            return {
                "access_token": data.get("access_token"),
                "refresh_token": data.get("refresh_token"),
                "expires_in": data.get("expires_in"),
                "token_type": data.get("token_type"),
                "scope": data.get("scope"),
            }

        error = response.json().get("error", "Unknown error") if response.headers.get("content-type", "").startswith("application/json") else "Invalid response"
        frappe.db.set_value("Integration Request", integration_request.name, "status", "Failed", update_modified=False)
        frappe.db.set_value("Integration Request", integration_request.name, "error", error, update_modified=False)
        frappe.throw(f"Authentication failed: <b>{error}</b>")

    except Exception as e:
        frappe.db.set_value("Integration Request", integration_request.name, {
            "status": "Failed",
            "error": str(e)
        }, update_modified=False)
        frappe.throw(f"Authentication request failed: <b>{e}</b>")


@frappe.whitelist()
def update_navari_settings_with_token(docname: str, skip_checks: bool = False) -> str:
    settings_doc = frappe.get_doc(SETTINGS_DOCTYPE_NAME, docname)
    needs_update = skip_checks or not settings_doc.get("access_token") or (
        datetime.strptime(
            str(settings_doc.get("token_expiry")).split(".")[0], "%Y-%m-%d %H:%M:%S"
        )
        < datetime.now()
    )
    if needs_update:
        auth_server_url = settings_doc.auth_server_url
        username = settings_doc.auth_username
        client_id = settings_doc.client_id
        password = settings_doc.get_password("auth_password")
        client_secret = settings_doc.get_password("client_secret")

        token_details = authenticate_and_get_token(
            auth_server_url, username, password, client_id, client_secret, docname
        )

        if not token_details:
            return None

        settings_doc.access_token = token_details["access_token"]
        settings_doc.refresh_token = token_details["refresh_token"]
        settings_doc.token_expiry = datetime.now() + timedelta(
            seconds=token_details["expires_in"]
        )
        settings_doc.save(ignore_permissions=True)

        user_details_fetch(docname)

    return settings_doc


@frappe.whitelist()
def user_details_fetch(document_name: str, **kwargs) -> None:
    from .apis.process_request import process_request

    request_data = {"document_name": document_name}

    return process_request(
        request_data,
        "BhfUserSearchReq",
        user_details_fetch_on_success,
        request_method="GET",
        settings_name=document_name,
        doctype=SETTINGS_DOCTYPE_NAME,
    )
    
@frappe.whitelist()
def user_details_fetch_on_success(response: dict, document_name: str, **kwargs) -> None:
    settings_doc = frappe.get_doc(SETTINGS_DOCTYPE_NAME, document_name)
    default_company = settings_doc.company  
    
    result = response.get("results", [])[0] if response.get("results") else response
    user_workstations = result.get("user_workstations") or []
    organisation_id = result.get("organisation_id")

    if not user_workstations:
        frappe.throw("No user workstations found in response.")

    existing_mappings = {
        mapping.workstation: mapping 
        for mapping in settings_doc.get("organisation_mapping", [])
    }

    processed_companies = set()  
    for workstation_entry in user_workstations:
        workstation_id = workstation_entry.get("workstation")
        cluster_id = workstation_entry.get("workstation__org_unit__parent__parent")
        
        if not workstation_id or not cluster_id:
            continue

        workstation_link = get_link_value(WORKSTATION_DOCTYPE_NAME, "slade_id", workstation_id)
        if not workstation_link:
            continue

        company_link = get_company_from_setup_mapping(cluster_id, document_name) or default_company
        
        if not company_link:
            continue

        branch_id = workstation_entry.get("workstation__org_unit__parent")
        branch_link = frappe.db.get_value("Branch", {"slade_id": branch_id, "company": company_link}, "name") if branch_id else None
        
        department_id = workstation_entry.get("workstation__org_unit")
        department_link = get_department(department_id, company_link) if department_id else None
        
        warehouse_link = get_default_warehouse(company_link)

        cluster_name = workstation_entry.get("workstation__org_unit__parent__parent__name")

        mapping_data = {
            "workstation": workstation_link,
            "organisation": organisation_id,
            "cluster": cluster_id,
            "cluster_name": cluster_name,
            "department": department_link,
            "company": company_link,
            "branch": branch_link,
            "warehouse": warehouse_link,
            "is_active": 1
        }

        if workstation_link in existing_mappings:
            update_existing_mapping(settings_doc.name, workstation_link, mapping_data)
        else:
            settings_doc.append("organisation_mapping", mapping_data)
            processed_companies.add(company_link)  
            settings_doc.save(ignore_permissions=True)
        
    update_company_slade_ids(processed_companies, organisation_id, settings_doc.name)
        
    frappe.db.commit()

def get_company_from_setup_mapping(cluster_id: str, setup_name: str) -> str:
    """Get company from active eTims Setup Mapping that matches cluster and setup"""
    mappings = frappe.get_all(
        "eTims Company Setup Mapping",
        filters={
            "etims_setup": setup_name,
            "cluster": cluster_id,
            "parenttype": "Company",
            "is_active": 1
        },
        fields=["parent"],
        distinct=True
    )
    
    return mappings[0].parent if mappings else None

def get_default_warehouse(company: str) -> str:
    """Get default warehouse for company"""
    warehouses = frappe.get_all(
        "Warehouse",
        filters={
            "is_group": 1,
            "company": company
        },
        fields=["name"],
        limit=1
    )
    return warehouses[0].name if warehouses else None

def update_existing_mapping(parent: str, workstation: str, data: dict) -> None:
    """Update existing organisation mapping"""
    mapping_name = frappe.get_value(
        ORGANISATION_MAPPING_DOCTYPE_NAME,
        filters={
            "parent": parent,
            "workstation": workstation
        },
        fieldname="name"
    )
    
    if mapping_name:
        frappe.db.set_value(ORGANISATION_MAPPING_DOCTYPE_NAME, mapping_name, data, update_modified=False)
        
def update_company_slade_ids(companies: set, organisation_id: str, setting_name: str) -> None:
    for company in companies:
        if not frappe.db.exists("Company", company):
            continue
      
        company_doc = frappe.get_doc("Company", company)
      
        existing_mapping = next(
            (m for m in company_doc.get("etims_setup_mapping", []) 
            if m.etims_setup == setting_name),
            None
        )
        
        if existing_mapping:
            frappe.db.set_value(
                "eTims Company Setup Mapping",
                existing_mapping.name,
                {
                    "organisation": organisation_id,
                    "is_active": 1
                }
            )
        else:
            company_doc.append("etims_setup_mapping", {
                "etims_setup": setting_name,
                "organisation": organisation_id,
                "is_active": 1
            })
            
        if not existing_mapping:
            company_doc.save(ignore_permissions=True)
            

def get_department(id: str, company: str) -> str:
    department_name = f"{company} - eTims Department"
    existing_department = frappe.db.get_value(
        "Department", {"department_name": department_name}, "name"
    )
    if existing_department:
        frappe.db.set_value("Department", existing_department, {
            "custom_slade_id": id,
            "custom_is_etims_department": 1,
            "company": company
        })
        return existing_department
    else:
        new_department = frappe.get_doc({
            "doctype": "Department",
            "department_name": department_name,
            "custom_slade_id": id,
            "custom_is_etims_department": 1,
            "company": company,
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        return new_department.name


def get_link_value(
    doctype: str, field_name: str, value: str, return_field: str = "name"
) -> str:
    try:
        return frappe.db.get_value(doctype, {field_name: value}, return_field)
    except Exception as e:
        frappe.log_error(
            title=f"Error Fetching Link for {doctype}",
            message=f"Error while fetching link for {doctype} with {field_name}={value}: {str(e)}",
        )
        return None


def get_or_create_link(doctype: str, field_name: str, value: str) -> str:
    if not value:
        return None

    try:
        link_name = frappe.db.get_value(doctype, {field_name: value}, "name")
        if not link_name:
            link_name = (
                frappe.get_doc(
                    {
                        "doctype": doctype,
                        field_name: value,
                        "code": value,
                    }
                )
                .insert(ignore_permissions=True, ignore_mandatory=True)
                .name
            )
            frappe.db.commit()
        return link_name
    except Exception as e:
        frappe.log_error(
            title=f"Error in get_or_create_link for {doctype}",
            message=f"Error in {doctype} - {value}: {str(e)}",
        )
        return None


def process_dynamic_url(route_path: str, request_data: dict | str) -> str:
    import json
    import re

    if isinstance(request_data, str):
        try:
            request_data = json.loads(request_data)
        except json.JSONDecodeError as e:
            raise ValueError("Invalid JSON string in request_data.") from e

    placeholders = re.findall(r"\{(.*?)\}", route_path)
    for placeholder in placeholders:
        if placeholder in request_data:
            route_path = route_path.replace(
                f"{{{placeholder}}}", str(request_data[placeholder])
            )
        else:
            raise ValueError(
                f"Missing required placeholder: '{placeholder}' in request_data."
            )

    return route_path


def generate_custom_item_code_etims(doc: Document) -> str:
    """Generate custom item code ETIMS based on the document fields"""
    new_prefix = f"{doc.custom_etims_country_of_origin_code}{doc.custom_product_type}{doc.custom_packaging_unit_code}{doc.custom_unit_of_quantity_code}"

    if doc.custom_item_code_etims:
        existing_suffix = doc.custom_item_code_etims[-7:]
    else:
        last_code = frappe.db.sql(
            """
            SELECT custom_item_code_etims
            FROM `tabItem`
            WHERE custom_item_classification = %s
            ORDER BY CAST(SUBSTRING(custom_item_code_etims, -7) AS UNSIGNED) DESC
            LIMIT 1
            """,
            (doc.custom_item_classification,),
        )
        last_code = last_code[0][0] if last_code else None
        if last_code:
            last_suffix = int(last_code[-7:])
            existing_suffix = str(last_suffix + 1).zfill(7)
        else:
            existing_suffix = "0000001"

    return f"{new_prefix}{existing_suffix}"



def get_total_stock_balance_from_sle(sle_name: str) -> dict:
    if not sle_name:
        return 0

    sle = frappe.db.get_value(
        "Stock Ledger Entry", 
        sle_name, 
        ["item_code", "creation"], 
        as_dict=True
    )

    if not sle:
        return 0

    item_code = sle["item_code"]
    creation = sle["creation"]

    warehouses = frappe.get_all(
        "Stock Ledger Entry",
        filters={
            "item_code": item_code,
            "docstatus": 1,
        },
        distinct=True,
        pluck="warehouse"
    )

    balance = 0

    for wh in warehouses:
        latest_sle = frappe.get_all(
            "Stock Ledger Entry",
            filters={
                "item_code": item_code,
                "warehouse": wh,
                "docstatus": 1,
                "creation": ("<=", creation),
            },
            fields=["qty_after_transaction"],
            order_by="posting_date desc, posting_time desc, creation desc",
            limit=1
        )

        if latest_sle:
            balance += float(latest_sle[0]["qty_after_transaction"])

    return round(balance, 4)


def get_max_submission_attempts(doctype: str = "Sales Invoice") -> int:
    settings = get_settings()
    if doctype == "Sales Invoice":
        tries = settings.get("maximum_sales_information_submission_attempts", 3)
    elif doctype == "Purchase Invoice":
        tries = settings.get("maximum_purchase_information_submission_attempts", 3)
    elif doctype == "Stock Ledger Entry":
        tries = settings.get("maximum_stock_information_submission_attempts", 3)
    else:
        tries = 3  
    return tries



def generate_strong_password(length: int = 16) -> str:
    """Generate a strong random password"""
    characters = string.ascii_letters + string.digits + string.punctuation
    while True:
        password = ''.join(secrets.choice(characters) for _ in range(length))
        if (any(c.islower() for c in password) and
            any(c.isupper() for c in password) and
            any(c.isdigit() for c in password) and
            any(c in string.punctuation for c in password)):
            return password

@frappe.whitelist()
def reset_auth_password(docname: str) -> None:
    settings_doc = frappe.get_doc(SETTINGS_DOCTYPE_NAME, docname)

    auth_server_url = settings_doc.auth_server_url
    old_password = settings_doc.get_password("auth_password")
    new_password = generate_strong_password()

    url = f"{auth_server_url}/password_change/"
    payload = {
        "old_password": old_password,
        "new_password1": new_password,
        "new_password2": new_password,
    }
    headers = {
        "Authorization": f"Bearer {settings_doc.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    integration_request = create_request_log(
        data=json.dumps(payload),
        request_description="Reset Slade360 Auth Password",
        is_remote_request=True,
        service_name="Slade360 eTims Password Reset",
        request_headers=json.dumps(headers),
        url=url,
        reference_doctype=SETTINGS_DOCTYPE_NAME,
        reference_docname=docname,
    )

    try:
        response = requests.post(url, headers=headers, json=payload)
        frappe.db.set_value("Integration Request", integration_request.name, "output", response.text, update_modified=False)

        if response.status_code == 200:
            frappe.db.set_value(SETTINGS_DOCTYPE_NAME, docname, "auth_password", new_password, update_modified=False)
            frappe.db.set_value("Integration Request", integration_request.name, "status", "Completed", update_modified=False)
        else:
            try:
                error_message = response.json().get("error", "Unknown error")
            except json.JSONDecodeError:
                error_message = f"Invalid response: {response.text}"

            frappe.db.set_value("Integration Request", integration_request.name, {
                "status": "Failed",
                "error": error_message
            }, update_modified=False)

            frappe.throw(f"Password update failed: <b>{error_message}</b>")

    except Exception as e:
        frappe.db.set_value("Integration Request", integration_request.name, {
            "status": "Failed",
            "error": str(e)
        }, update_modified=False)
        frappe.throw(f"Password update request failed: <b>{e}</b>")


def get_slade360_id(doctype: str, name: str, setting: str) -> str:        
    if not frappe.db.exists(doctype, name):
        frappe.throw(_("Document {0} with name {1} does not exist.").format(doctype, name))
    
    slade_id = frappe.db.get_value(
        SLADE_ID_MAPPING_DOCTYPE_NAME,
        filters={
            "etims_setup": setting,
            "parenttype": doctype,
            "parent": name
        },
        fieldname="slade360_id"
    )
    
    return slade_id


def get_parent_by_slade360_id(doctype: str, slade360_id: str, setting: str) -> str:
    """Returns the parent document name for a given Slade360 ID.
    
    Args:
        doctype (str): The parent doctype
        slade360_id (str): The Slade360 ID to search for
        setting (str): The eTims setting name
        
    Returns:
        str: The parent document name if found, None otherwise
    """
    parent_name = frappe.db.get_value(
        SLADE_ID_MAPPING_DOCTYPE_NAME,
        filters={
            "etims_setup": setting,
            "parenttype": doctype,
            "slade360_id": slade360_id
        },
        fieldname="parent"
    )
    
    return parent_name



@frappe.whitelist()
def get_etims_action_data(doctype: str, docname: str = None) -> dict[str, Any]:
    active_settings = get_active_settings()
    if not docname:
        return {
            "settings": active_settings,
            "has_mappings": False,
            "registered_mappings": [],
            "unregistered_settings": []
        }
    try:
        doc = frappe.get_doc(doctype, docname)
    except frappe.DoesNotExistError:
        frappe.throw(f"{doctype} '{docname}' does not exist.")


    if not active_settings:
        return {
            "settings": [],
            "has_mappings": False,
            "registered_mappings": [],
            "unregistered_settings": []
        }

    active_setting_names = [s["name"] for s in active_settings]

    registered_mappings = []
    registered_setup_names = set()

    for row in getattr(doc, "etims_setup_mapping", []):
        if row.etims_setup in active_setting_names:
            registered_mappings.append({
                "etims_setup": row.etims_setup,
                "slade360_id": row.slade360_id,
                "name": row.name
            })
            registered_setup_names.add(row.etims_setup)

    unregistered_settings = [
        s for s in active_settings if s["name"] not in registered_setup_names
    ]

    return {
        "settings": active_settings,
        "has_mappings": bool(registered_mappings),
        "registered_mappings": registered_mappings,
        "unregistered_settings": unregistered_settings
    }


def parse_response_data(
    response: Union[str, bytes, dict, list], 
    expected_type: type = list
) -> Union[List[Any], Dict[str, Any], Any]:
    """Parse and convert response data to expected type using standard json.
    
    Args:
        response: Input data (JSON string, bytes, or Python object)
        expected_type: Desired output type (list, dict, or other)
        
    Returns:
        Data converted to expected type
        
    Raises:
        ValueError: If JSON parsing fails
        TypeError: If type conversion fails
    """
    if isinstance(response, (str, bytes)):
        try:
            response = json.loads(response)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {str(e)}") from e
    
    if response is None:
        return expected_type()
    
    try:
        if expected_type is list:
            if isinstance(response, dict):
                return response.get('results', [response])
            return response if isinstance(response, list) else [response]
            
        elif expected_type is dict:
            if isinstance(response, list):
                return response[0] if response else {}
            return response if isinstance(response, dict) else {'data': response}
            
        return expected_type(response) if response else expected_type()
        
    except (TypeError, AttributeError) as e:
        raise TypeError(f"Cannot convert to {expected_type}: {str(e)}") from e 
        

def build_item_payload(item, settings_name: str, slade_id: str = None) -> dict:
    """Construct the payload for item registration"""
    selling_price = round(item.get("valuation_rate", 1), 2) or 1
    purchasing_price = round(item.get("last_purchase_rate", 1), 2)
    tax = get_slade360_id(
        TAXATION_TYPE_DOCTYPE_NAME, 
        item.get("custom_taxation_type"), 
        settings_name
    )
    id = slade_id or next((row.slade360_id for row in item.etims_setup_mapping if row.etims_setup == settings_name), None)

    payload = {
        "name": item.name,
        "document_name": item.name,
        "description": item.description,
        "can_be_sold": bool(item.is_sales_item),
        "can_be_purchased": bool(item.is_purchase_item),
        "company_name": frappe.defaults.get_user_default("Company"),
        "code": item.item_code,
        "scu_item_code": item.custom_item_code_etims,
        "scu_item_classification": get_slade360_id(
            ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
            item.custom_item_classification,
            settings_name,
        ),
        "product_type": item.custom_product_type,
        "item_type": item.custom_item_type,
        "preferred_name": item.item_name,
        "country_of_origin": item.custom_etims_country_of_origin_code,
        "selling_price": selling_price,
        "packaging_unit": get_slade360_id(
            PACKAGING_UNIT_DOCTYPE_NAME,
            item.custom_packaging_unit,
            settings_name,
        ),
        "quantity_unit": get_slade360_id(
            UNIT_OF_QUANTITY_DOCTYPE_NAME,
            item.custom_unit_of_quantity,
            settings_name,
        ),
        "purchasing_price": purchasing_price,
        "categories": [],
        "purchase_taxes": [],
        "sale_taxes": [tax] if tax else [],
    }

    if id:
        payload["id"] = id

    return payload
    

def build_partner_payload(data, settings_name: str, is_customer: bool = True, existing_id: str = None) -> dict:
    """Build payload for customer/supplier data submission to Slade
    
    Args:
        data: The document containing partner data
        settings_name (str): The name of the eTims settings
        is_customer (bool): Whether the partner is a customer
        existing_id (str): Existing Slade360 ID if available
        
    Returns:
        dict: The payload for the API request
    """
    payload = {
        "document_name": data.name,
        "currency": data.get("default_currency") or "KES",
        "country": "KEN",
    }

    partner_type_mapping = {
        "Company": "CORPORATE",
        "Individual": "INDIVIDUAL",
        "Partnership": "CORPORATE",
    }

    if is_customer:
        customer_type = data.get("customer_type")
        mapped_customer_type = partner_type_mapping.get(customer_type, customer_type)

        payload.update({
            "is_customer": True,
            "customer_tax_pin": data.get("tax_id"),
            "partner_name": data.get("customer_name"),
            "phone_number": data.get("mobile_no"),
            "customer_type": mapped_customer_type,
        })
    else:
        supplier_type = data.get("supplier_type")
        mapped_supplier_type = partner_type_mapping.get(supplier_type, supplier_type)

        payload.update({
            "customer_tax_pin": data.get("tax_id"),
            "partner_name": data.get("supplier_name"),
            "is_supplier": True,
            "supplier_type": mapped_supplier_type,
        })

    phone_number = (data.get("phone_number") or "").replace(" ", "").strip()
    payload["phone_number"] = (
        "+254" + phone_number[-9:] if len(phone_number) >= 9 else None
    )

    currency_name = get_slade360_id(
        "Currency",
        payload.get("currency"),
        settings_name,
    )
    
    if currency_name:
        payload["currency"] = currency_name[0] if isinstance(currency_name, (list, tuple)) else currency_name
    
    id = existing_id or next((row.slade360_id for row in data.etims_setup_mapping if row.etims_setup == settings_name), None)
    if id:
        payload["id"] = id
        
    return payload


def get_invoice_reference_number(invoice: Document) -> str:
    """
    Generate a unique reference number for the invoice submission.

    - If the invoice has no revisions, the reference is simply the document name.
    - If the invoice has revisions (revision_count > 0), append `-REV{revision_count}` 
      to make it unique and traceable (e.g., SINV-0001-REV1).

    Args:
        invoice (Document): The Invoice document instance.

    Returns:
        str: The generated reference number for submission.
    """
    reference_number = invoice.name
    if hasattr(invoice, "revision_count") and invoice.revision_count is not None and int(invoice.revision_count) > 0:
        reference_number = f"{invoice.name}-REV{int(invoice.revision_count)}"
    return reference_number


def build_return_invoice_payload(invoice: Document, kra_invoice_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a return invoice payload for eTims.
    
    - For full returns: Use original KRA invoice lines with actual prices/quantities from KRA.
    - For partial returns: Use ERPNext return invoice data only.
    
    Args:
        invoice (Document): The ERPNext Sales Invoice document (return type).
        kra_invoice_data (dict): The original KRA invoice response.
    
    Returns:
        dict: The payload to submit to eTims for a return invoice.
    """
    original_invoice = frappe.get_doc("Sales Invoice", invoice.return_against)
    original_invoice_total = abs(float(original_invoice.base_grand_total))
    return_total = abs(float(invoice.base_grand_total))
    is_full_return = abs(original_invoice_total - return_total) < 0.01
    reference_number = get_invoice_reference_number(original_invoice)
    amount = (
        float(kra_invoice_data.get("total_gross_amount", 0))
        if is_full_return and "total_gross_amount" in kra_invoice_data
        else return_total
    )
    return prepare_return_invoice_payload(
        document_name=invoice.name,
        reference_number=reference_number,
        amount=amount,
        invoice=invoice,
        kra_invoice_data=kra_invoice_data,
        is_full_return=is_full_return
    )


def prepare_return_invoice_payload(
    document_name: str,
    reference_number: str,
    amount: float,
    invoice: Document,
    kra_invoice_data: Dict[str, Any],
    is_full_return: bool
) -> Dict[str, Any]:
    items = []
    if is_full_return:
        for line in kra_invoice_data.get("sales_invoice_lines", []):
            items.append({
                "item_name": line.get("product_name"),
                "quantity": abs(line.get("quantity", 0)),
                "amount": round(abs(line.get("price_inclusive_tax", 0)), 4),
            })
    else:
        for item in invoice.items:
            tax_amount = item.get("custom_tax_amount", 0) or 0
            qty = abs(item.get("qty"))
            base_amount = round(abs(item.get("base_amount")) or 0, 4)
            items.append({
                "item_name": item.item_code,
                "quantity": qty,
                "amount": round(base_amount + tax_amount, 4),
            })

    return {
        "document_name": document_name,
        "invoice_reference": reference_number,
        "refund_reason": "13",
        "amount": amount,
        "items": items,
    }