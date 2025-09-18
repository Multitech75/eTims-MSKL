"""Utility functions"""

import json
import re
import secrets
import string
from base64 import b64encode
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from io import BytesIO
from typing import Any, Dict, List, Union, Literal
from urllib.parse import urlencode

import qrcode
import requests

import frappe
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import now_datetime

from .doctype.doctype_names_mapping import (
    SETTINGS_DOCTYPE_NAME,
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




def get_settings(company_name: str = None) -> dict | None:
    """Fetch active settings for a given company.

    Args:
        company_name (str, optional): The name of the company. Defaults to user's default or first available.

    Returns:
        dict | None: The settings if found, otherwise None.
    """ 
    company_name = (
        company_name
        or frappe.defaults.get_user_default("Company")
        or frappe.get_value("Company", {}, "name")
    )

    if not company_name:
        return None

    return frappe.db.get_value(
        SETTINGS_DOCTYPE_NAME,
        {"company_name": company_name},
        "*",
        as_dict=True,
    )


        

@frappe.whitelist()
def get_active_settings(company_name: str = None) -> list[dict]:
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




"""
    START OF SALES INVOICE PAYLOAD BUILDING AND TAX CALCULATION
"""
def build_invoice_payload(
    invoice: Document,
    invoice_type: Literal["Sales Invoice", "POS Invoice"]
) -> dict:
    reference_number = get_invoice_reference_number(invoice)
    paymentType = "02" if invoice_type == "Sales Invoice" else "01"
    # get current datetime (server time)
    dt = now_datetime()
    dateOnly = dt.strftime("%Y%m%d")
    dateTime = f"{dateOnly}120000"
    payload = {
        "customerNo": frappe.get_value("Customer", invoice.customer, "name") or None,
        "customerTin": invoice.tax_id or "", 
        "customerName": frappe.get_value("Customer", invoice.customer, "customer_name") or None,
        "customerMobileNo": "",
        "salesType": "N",
        "paymentType": paymentType, #"02", #01- CASH, 02- CREDIT
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
        etims_log("Debug", "build_invoice_payload tax_code item", tax_amount,qty,base_net_rate,item)
        # tax_code = item.get("taxation_type_code", "A") or "A"
        item_doc = frappe.get_doc("Item", item.item_code)
        tax_code = item_doc.custom_eTims_tax_code or ""
        if not tax_code:
            frappe.throw(
                msg=f"Item {item.item_name} does not have a valid eTims Tax Code. Please update the item before submitting the invoice.",
                title="eTims Error"
            )

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


    """ END OF SALES INVOICE PAYLOAD BUILDING AND TAX CALCULATION"""




"""
    START OF SALES CREDITNOTE PAYLOAD BUILDING AND TAX CALCULATION
"""

def build_creditnote_payload(
    invoice: Document, invoice_type: Literal["Sales Invoice", "POS Invoice"],reference_number: str = None
) -> dict:
    # get current datetime (server time)
    paymentType = "02" if invoice_type == "Sales Invoice" else "01"
    creditNoteReason = "11" if invoice_type == "Sales Invoice" else "06"
    dt = now_datetime()
    dateOnly = dt.strftime("%Y%m%d")
    dateTime = f"{dateOnly}120000"
    payload = {
        "orgInvoiceNo": reference_number,
        "traderInvoiceNo": invoice.name,
        "salesType": "N",
        "paymentType": paymentType, #01- CASH, 02- CREDIT
        "creditNoteDate": dateTime,
        "confirmDate": dateTime,
        "salesDate": dateOnly,
        "stockReleseDate": dateTime,
        "receiptPublishDate": dateTime,
        "occurredDate": dateOnly,
        "creditNoteReason": creditNoteReason,
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
        etims_log("Debug", "build_invoice_payload tax_code item", tax_amount,qty,base_net_rate,item)
        payload["creditNoteItemsList"].append({
            "itemCode": item.item_code,
            "unitPrice": round(base_net_rate + ((tax_amount * -1) / qty if qty else 0), 4),
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
        _calculate_item_level_taxes(doc) #Calculate taxes using item tax templates
    elif taxes:
        _calculate_document_level_taxes(doc, taxes) #Distribute document-level taxes across items
    
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
            # _get_taxation_type_from_template(item) or
            # _get_taxation_type_from_rate(item) or
            _get_taxation_type_from_item(item) or
            "A" # fallback default
        )
        # item_doc = frappe.get_doc("Item",item.item_code )
        etims_log("Debug", "_set_taxation_type_codes item.taxation_type_code", item.taxation_type_code)

        # # Item tax template (if set)
        # tax_template = item_doc.get("taxes")
        # etims_log("Debug", "_set_taxation_type_codes taxes", tax_template)
        # if tax_template:
        #     for tax in tax_template:
        #         etims_log("Debug", "_set_taxation_type_codes taxes", tax.tax_type, tax.tax_rate)
        #         print("Account Head:", tax.tax_type, " | Tax Rate:", tax.tax_rate)


def _get_taxation_type_from_item(item) -> str:
    """Get taxation type from item master data if available"""
    return frappe.get_value("Item", item.item_code, "custom_eTims_tax_code") or ""

def _get_taxation_type_from_template(item) -> str:
    """Get taxation type from item's tax template if available"""
    etims_log("Debug", "_get_taxation_type_from_template", item.item_tax_template)
    if item.item_tax_template:
        return frappe.get_value("Item Tax Template", item.item_tax_template, "custom_eTims_tax_code")
    return ""


def _get_taxation_type_from_rate(item) -> str:
    """Determine taxation type based on item's tax rate"""
    if not hasattr(item, 'custom_eTims_tax_code'):
        return ""
    etims_log("Debug", "_get_taxation_type_from_rate", item.custom_eTims_tax_code)
    if round(item.custom_eTims_tax_code) >= 16:
        return "B"
    elif round(item.custom_eTims_tax_code) >= 8:
        return "E"
    elif item.custom_eTims_tax_code == 0:
        return "A"
    return ""




    """ END OF ITEM TAX CALCULATION"""



