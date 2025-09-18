from typing import Literal

import frappe
from frappe.model.document import Document
import asyncio
import json
from datetime import datetime
from io import BytesIO
import qrcode
from ...apis.apis import send_payload_to_etims

from ...utils import build_invoice_payload,build_creditnote_payload, get_invoice_reference_number
from ...logger import etims_log
from typing import Literal


def generic_invoices_before_submit( 
    doc: Document,
    settings_doc: dict | None,
    invoice_type: Literal["Sales Invoice", "POS Invoice"],
    method=None,
) -> None:
    """Handle sending of Sales Invoice or POS Invoice data to eTims before submission."""

    etims_log("Debug", "generic_invoices_before_submit doc & invoice_type", {"doc": doc.name, "type": invoice_type})

    if doc.prevent_etims_submission or (hasattr(doc, "etr_invoice_number") and doc.etr_invoice_number) or doc.status == "Credit Note Issued":
        return
    
    # --- Process Returns ---
    if doc.is_return:
        return_invoice = frappe.get_doc(invoice_type, doc.return_against)
        if not return_invoice.custom_successfully_submitted:
            frappe.throw(
                f"Return against invoice {doc.return_against} was not successfully submitted. Cannot process return."
            )

        reference_number = return_invoice.custom_scu_invoice_number
        payload = build_creditnote_payload(doc,invoice_type, reference_number)

        etims_log("Debug", "generic_invoices_before_submit creditnote payload", payload)
        etims_log("Debug", "generic_invoices_before_submit reference_number", reference_number)

        api_url = f"{settings_doc.get('etims_url', '').rstrip('/')}/AddSaleCreditNoteV2"
        api_key=settings_doc.get("api_key")
        response = send_payload_to_etims(payload, api_url,api_key)

    # --- Normal Submission ---
    else:
        invoice_number = get_invoice_reference_number(doc)
        payload = build_invoice_payload(doc,invoice_type)

        etims_log("Debug", "generic_invoices_before_submit payload", payload)
        etims_log("Debug", "generic_invoices_before_submit invoice_number", invoice_number)

        api_url = f"{settings_doc.get('etims_url', '').rstrip('/')}/AddSaleV2"
        api_key=settings_doc.get("api_key")
        response = send_payload_to_etims(payload, api_url,api_key)
    
    # --- Block submission if failed ---
    if not response.get("status"):
        frappe.throw(
            msg=f"Failed to validate {invoice_type} {doc.name} in eTims.<br>{response.get('message')}",
            title="eTims Error"
        )

    etims_log("Debug", "generic_invoices_before_submit response", response)

    # --- Success: update invoice fields ---
    handle_etims_success_response(doc, response, invoice_type)


def handle_etims_success_response(doc, response: dict, doctype: str):
    """Handle common eTims response (success & failure) for Invoices, Stock Entry, etc."""

    # --- Success: update fields ---
    resp = response.get("responseData") or {}
    qr_url = resp.get("scuqrCode")
    image_url = None

    if qr_url:
        image_url = generate_and_attach_qr_code(qr_url, doc.name, doctype)

    sdc_datetime = None
    if resp.get("sdcDateTime"):
        try:
            parsed_dt = datetime.strptime(resp["sdcDateTime"], "%Y%m%d%H%M%S")
            sdc_datetime = parsed_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            etims_log("Error", f"Invalid sdcDateTime format: {resp['sdcDateTime']}")

    doc.custom_successfully_submitted = 1
    doc.custom_invoice_eTims_message = response.get("message")
    doc.custom_current_receipt_number = str(resp.get("curRecptNo"))
    doc.custom_total_receipt_number = str(resp.get("totRecptNo"))
    doc.custom_control_unit_date_time = sdc_datetime
    doc.custom_scu_invoice_number = resp.get("invoiceNo")
    doc.custom_scu_original_invoice_number = resp.get("originalInvoiceNo") or ""
    doc.custom_receipt_signature = resp.get("scuReceiptSignature")
    doc.custom_internal_data = resp.get("scuInternalData")
    doc.custom_qr_code_url = qr_url
    doc.custom_qr_code = image_url
    doc.custom_eTims_response = frappe.as_json(response)
    doc.custom_scu_id = resp.get("sdcid")
    doc.custom_scu_mrc_no = resp.get("sdcmrcNo")


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


