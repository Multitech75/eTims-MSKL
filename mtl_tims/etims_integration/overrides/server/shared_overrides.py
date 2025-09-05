from typing import Literal

import frappe
from frappe.model.document import Document
import asyncio
import json
from datetime import datetime
from io import BytesIO
import qrcode
from ...apis.apis import send_invoice_to_etims
from ...apis.api_builder import EndpointsBuilder
from ...apis.process_request import process_request
from ...apis.remote_response_status_handlers import (
    sales_information_submission_on_success,
    sales_information_submission_on_error,
)
# from ...doctype.doctype_names_mapping import SETTINGS_DOCTYPE_NAME
from ...utils import build_invoice_payload,build_creditnote_payload, get_invoice_reference_number, get_settings
from ...logger import etims_log
endpoints_builder = EndpointsBuilder()

def generic_invoices_before_submit(
    doc: Document, invoice_type: Literal["Sales Invoice", "POS Invoice"], method=None
) -> None:
    """Handle sending of Sales Invoice data to eTims before submission."""

    etims_log("Debug", "generic_invoices_on_submit_override doc & invoice_type", doc)

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
        
        payload = build_creditnote_payload(doc,reference_number)
        etims_log("Debug", "generic_invoices_on_submit_override payload", payload)
        etims_log("Debug", "generic_invoices_on_submit_override reference_number,return_invoice.custom_scu_invoice_number", reference_number,return_invoice.custom_scu_invoice_number)
        api_url = "http://41.139.135.45:8089/api/AddSaleCreditNoteV2"
        response = send_invoice_to_etims(payload,api_url)

    # --- Normal Submission ---
    else:
        invoice_number = get_invoice_reference_number(doc)
        payload = build_invoice_payload(doc)
        api_url = "http://41.139.135.45:8089/api/AddSaleV2"
        response = send_invoice_to_etims(payload,api_url)
    
    # --- Block submission if failed ---
    if not response.get("status"):
        frappe.throw(
            msg=f"❌ Failed to validate invoice {doc.name} in eTims.<br>{response.get('message')}",
            title="eTims Error"
        )

    etims_log("Debug", "generic_invoices_on_submit_override response", response)
    # --- Success: update invoice fields ---
    handle_etims_success_response(doc, response, "Sales Invoice")


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
































# def generic_invoices_on_submit_override(
#     doc: Document, invoice_type: Literal["Sales Invoice", "POS Invoice"]
# ) -> None:
#     """Handle sending of Sales information from invoice documents to eTims"""

#     etims_log("Debug", "generic_invoices_on_submit_override doc & invoice_type", doc, invoice_type)

#     if doc.prevent_etims_submission or (hasattr(doc, "etr_invoice_number") and doc.etr_invoice_number) or doc.status == "Credit Note Issued":
#         return

#     # --- Process Returns ---
#     if doc.is_return:
#         return_invoice = frappe.get_doc(invoice_type, doc.return_against)
#         if not return_invoice.custom_successfully_submitted:
#             frappe.throw(
#                 f"Return against invoice {doc.return_against} was not successfully submitted. Cannot process return."
#             )

#         from ...apis.apis import submit_credit_note
#         reference_number = get_invoice_reference_number(return_invoice)
#         request_data = {
#             "document_name": doc.name,
#             "company": doc.company,
#             "reference_number": reference_number,
#         }
#         frappe.enqueue(
#             process_request,
#             queue="default",
#             is_async=True,
#             request_data=request_data,
#             route_key="TrnsSalesSaveWrReq",
#             handler_function=submit_credit_note,
#             doctype=invoice_type,
#             settings_name=get_settings(doc.company).name,
#         )

#     # --- Normal Submission ---
#     else:
#         payload = build_invoice_payload(doc)
#         etims_log("Debug", "generic_invoices_on_submit_override payload", payload)
#         invoice_number = get_invoice_reference_number(doc)
#         send_invoice_to_etims(payload, invoice_number, invoice_type)

# def generic_invoices_on_submit_override(
#     doc: Document, invoice_type: Literal["Sales Invoice", "POS Invoice"]
# ) -> None:
#     """Defines a function to handle sending of Sales information from relevant invoice documents

#     Args:
#         doc (Document): The doctype object or record
#         invoice_type (Literal["Sales Invoice", "POS Invoice"]):
#         The Type of the invoice. Either Sales, or POS
#     """
#     etims_log("Debug", "generic_invoices_on_submit_override doc & invoice_type", doc,invoice_type)
#     # company_name = (
#     #     doc.company
#     #     or frappe.defaults.get_user_default("Company")
#     #     or frappe.get_value("Company", {}, "name")
#     # )

#     # settings_doc = get_settings(company_name=company_name)
#     if doc.prevent_etims_submission or (hasattr(doc, "etr_invoice_number") and doc.etr_invoice_number) or doc.status == "Credit Note Issued":
#         return


#     for item in doc.items:
#         etims_log("Debug", "generic_invoices_on_submit_override item", item)
#         item_doc = frappe.get_doc("Item", item.item_code)
        
#         etims_log("Debug", "generic_invoices_on_submit_override item_doc.name", item_doc.name)
#         etims_log("Debug", "generic_invoices_on_submit_override custom_item_code_etims & item", item_doc.custom_item_code_etims)
#         if not item_doc.custom_item_code_etims:
#             from ...apis.apis import perform_item_registration 

#             perform_item_registration(item_doc.name)
#             # Stop submission
#             frappe.throw(
#                 f"Item {item.item_code} is not registered in eTims. Invoice cannot be submitted."
#             )


#     if doc.is_return:
#         return_invoice = frappe.get_doc(invoice_type, doc.return_against)
#         if not return_invoice.custom_successfully_submitted:
#             frappe.msgprint(
#                 f"Return against invoice {doc.return_against} was not successfully submitted. Cannot process return."
#             )
#             return
        
#         from ...apis.apis import submit_credit_note
#         reference_number = get_invoice_reference_number(return_invoice)
#         request_data = {
#             "document_name": doc.name,
#             "company": company_name,
#             "reference_number": reference_number,
#         }
#         frappe.enqueue(
#             process_request,
#             queue="default",
#             is_async=True,
#             request_data=request_data,
#             route_key="TrnsSalesSaveWrReq",
#             handler_function=submit_credit_note,
#             doctype=invoice_type,
#             settings_name=settings_doc.name,
#         )
        
#     else:
#         payload = build_invoice_payload(doc)
#         etims_log("Debug", "generic_invoices_on_submit_override payload", payload)
#         invoice_number = get_invoice_reference_number(doc)
#         send_invoice_to_etims(payload,invoice_number,invoice_type)
        # additional_context = {
        #     "invoice_type": invoice_type,
        # }
        # process_request(
        #     payload,
        #     "SalesInvoiceSaveReq",
        #     lambda response, **kwargs: sales_information_submission_on_success(
        #         response=response,
        #         **additional_context,
        #         **kwargs,
        #     ),
        #     request_method="POST",
        #     doctype=invoice_type,
        #     settings_name=settings_doc.name,
        #     company=company_name,
        #     error_callback=sales_information_submission_on_error,
        # )


def validate(doc: Document, method: str) -> None:
    pass
    # vendor = ""
    # doc.custom_scu_id = get_curr_env_etims_settings(
    #     frappe.defaults.get_user_default("Company"), vendor, doc.branch
    # ).scu_id

    # item_taxes = get_itemised_tax_breakup_data(doc)

    # taxes_breakdown = defaultdict(list)
    # taxable_breakdown = defaultdict(list)
    # tax_head = doc.taxes[0].description

    # for index, item in enumerate(doc.items):
    #     taxes_breakdown[item.custom_taxation_type_code].append(
    #         item_taxes[index][tax_head]["tax_amount"]
    #     )
    #     taxable_breakdown[item.custom_taxation_type_code].append(
    #         item_taxes[index]["taxable_amount"]
    #     )

    # update_tax_breakdowns(doc, (taxes_breakdown, taxable_breakdown))


# def update_tax_breakdowns(invoice: Document, mapping: tuple) -> None:
#     invoice.custom_tax_a = round(sum(mapping[0]["A"]), 2)
#     invoice.custom_tax_b = round(sum(mapping[0]["B"]), 2)
#     invoice.custom_tax_c = round(sum(mapping[0]["C"]), 2)
#     invoice.custom_tax_d = round(sum(mapping[0]["D"]), 2)
#     invoice.custom_tax_e = round(sum(mapping[0]["E"]), 2)

#     invoice.custom_taxbl_amount_a = round(sum(mapping[1]["A"]), 2)
#     invoice.custom_taxbl_amount_b = round(sum(mapping[1]["B"]), 2)
#     invoice.custom_taxbl_amount_c = round(sum(mapping[1]["C"]), 2)
#     invoice.custom_taxbl_amount_d = round(sum(mapping[1]["D"]), 2)
#     invoice.custom_taxbl_amount_e = round(sum(mapping[1]["E"]), 2)
