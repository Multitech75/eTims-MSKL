from datetime import datetime
from io import BytesIO

import deprecation
import frappe
import qrcode
import time

from ... import __version__
from ..doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
    IMPORTED_ITEMS_STATUS_DOCTYPE_NAME,
    ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
    NOTICES_DOCTYPE_NAME,
    OPERATION_TYPE_DOCTYPE_NAME,
    PACKAGING_UNIT_DOCTYPE_NAME,
    REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME,
    REGISTERED_PURCHASES_DOCTYPE_NAME,
    REGISTERED_PURCHASES_DOCTYPE_NAME_ITEM,
    SLADE_ID_MAPPING_DOCTYPE_NAME,
    TAXATION_TYPE_DOCTYPE_NAME,
    UNIT_OF_QUANTITY_DOCTYPE_NAME,
    USER_DOCTYPE_NAME,
)
from ..handlers import handle_slade_errors
from ..utils import (
    build_item_payload,
    build_partner_payload,
    build_return_invoice_payload,
    build_invoice_payload,
    get_link_value,
    get_or_create_link,
    get_parent_by_slade360_id,
    get_slade360_id,
    parse_response_data,
    prepare_return_invoice_payload,
)


def on_slade_error(
    response: dict | str,
    url: str | None = None,
    doctype: str | None = None,
    document_name: str | None = None,
) -> None:
    """Base "on-error" callback.on_slade_error

    Args:
        response (dict | str): The remote response
        url (str | None, optional): The remote address. Defaults to None.
        doctype (str | None, optional): The doctype calling the remote address. Defaults to None.
        document_name (str | None, optional): The document calling the remote address. Defaults to None.
        integration_reqeust_name (str | None, optional): The created Integration Request document name. Defaults to None.
    """
    handle_slade_errors(
        response,
        route=url,
        doctype=doctype,
        document_name=document_name,
    )


"""
These functions are required as serialising lambda expressions is a bit involving.
"""


def customer_search_on_success(response: dict, document_name: str, **kwargs) -> None:
    frappe.db.set_value(
        "Customer",
        document_name,
        {
            "custom_tax_payers_name": response["partner_name"],
            # "custom_tax_payers_status": response["taxprSttsCd"],
            "custom_county_name": response["town"],
            "custom_subcounty_name": response["town"],
            "custom_tax_locality_name": response["physical_address"],
            "custom_location_name": response["physical_address"],
            "custom_is_validated": 1,
        },
    )


def update_document_mapping(doc_type: str, document_name: str, settings_name: str, slade_id: str) -> None:
    """Common function to update document mapping with Slade IDs
    
    Args:
        doc_type (str): The document type to update
        document_name (str): The name of the document to update
        settings_name (str): The name of the eTims settings
        slade_id (str): The Slade ID to set
    """
    doc = frappe.get_doc(doc_type, document_name)
    found = False
    for row in doc.get("etims_setup_mapping", []):
        if row.etims_setup == settings_name:
            frappe.db.set_value(
                SLADE_ID_MAPPING_DOCTYPE_NAME,
                row.name,
                {
                    "slade360_id": slade_id,
                },
            )
            found = True
            break

    if not found:
        doc.append("etims_setup_mapping", {
            "etims_setup": settings_name,
            "slade360_id": slade_id,
        })
        doc.save(ignore_permissions=True)
        
    return doc


def item_registration_on_success(response: dict, document_name: str, settings_name: str, **kwargs) -> None:
    item = update_document_mapping("Item", document_name, settings_name, response.get("id"))
    
    if item.is_stock_item:
        frappe.enqueue(
            "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.submit_inventory",
            name=document_name,
            queue="long",
        )


def customer_branch_details_submission_on_success(
    response: dict, document_name: str, settings_name: str, **kwargs
) -> None:
    doctype = "Supplier" if response.get("is_supplier") else "Customer"
    update_document_mapping(doctype, document_name, settings_name, response.get("id"))


def user_details_submission_on_success(
    response: dict, document_name: str, **kwargs
) -> None:
    frappe.db.set_value(
        USER_DOCTYPE_NAME,
        document_name,
        {
            "submitted_successfully_to_etims": (
                1 if response.get("sent_to_etims") else 0
            ),
            "slade_id": response.get("id"),
            "sent_to_slade": 1,
        },
    )


def user_details_fetch_on_success(response: dict, document_name: str, **kwargs) -> None:
    result = response.get("results", [])[0] if response.get("results") else response
    email = result.get("email")

    existing_user = frappe.db.exists("User", {"email": email})
    if not existing_user:
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": result.get("first_name"),
                "last_name": result.get("last_name"),
                "full_name": result.get("full_name"),
                "enabled": 1,
                "roles": [{"role": "System Manager"}],
            }
        )
        user.insert(ignore_permissions=True)
    else:
        user = frappe.get_doc("User", existing_user)

    workstation = (
        result.get("user_workstations")[0]["workstation"]
        if result.get("user_workstations") and len(result.get("user_workstations")) > 0
        else None
    )

    branch_id = (
        result.get("user_workstations")[0]["workstation__org_unit__parent"]
        if result.get("user_workstations") and len(result.get("user_workstations")) > 0
        else None
    )

    data = {
        "submitted_successfully_to_etims": 1 if response.get("sent_to_etims") else 0,
        "slade_id": result.get("id"),
        "sent_to_slade": 1,
        "first_name": result.get("first_name"),
        "last_name": result.get("last_name"),
        "users_full_names": result.get("full_name"),
        "email": email,
        "workstation": workstation,
        "company": get_link_value(
            "Company", "custom_slade_id", result.get("organisation_id")
        ),
        "branch": get_link_value("Branch", "slade_id", branch_id),
        "system_user": user.name,
    }

    existing_doc = frappe.db.exists("Navari eTims User", {"email": email})
    if not existing_doc:
        new_doc = frappe.get_doc({"doctype": "Navari eTims User", **data})
        new_doc.insert(ignore_permissions=True)
    else:
        frappe.db.set_value("Navari eTims User", existing_doc, data)


@deprecation.deprecated(
    deprecated_in="0.6.6",
    removed_in="1.0.0",
    current_version=__version__,
    details="Callback became redundant due to changes in the Item doctype rendering the field obsolete",
)
def inventory_submission_on_success(
    response: dict, document_name: str, **kwargs
) -> None:
    frappe.db.set_value("Item", document_name, {"custom_inventory_submitted": 1})


def imported_item_submission_on_success(
    response: dict, document_name: str, **kwargs
) -> None:
    frappe.db.set_value("Item", document_name, {"custom_imported_item_submitted": 1})


def submit_inventory_on_success(response: dict, document_name: str, settings_name: str, **kwargs) -> None:
    from .process_request import process_request

    # item = frappe.get_doc("Item", document_name)
    stock_levels = frappe.db.get_all(
        "Bin",
        filters={"item_code": document_name},
        fields=["actual_qty"],
    )

    request_data = {
        "document_name": document_name,
        "product": get_slade360_id("Item", document_name, settings_name),
        "quantity": sum([float(stock.get("actual_qty", 0)) for stock in stock_levels]),
        "inventory_adjustment": response.get("id"),
    }

    frappe.enqueue(
        process_request,
        queue="default",
        is_async=True,
        doctype="Item",
        request_data=request_data,
        route_key="StockMasterLineReq",
        handler_function=submit_inventory_item_on_success,
        request_method="POST",
        settings_name=settings_name,
    )


def submit_inventory_item_on_success(
    response: dict, document_name: str, settings_name: str, **kwargs
) -> None:
    from .process_request import process_request

    doc = frappe.get_doc("Item", document_name)
    request_data = {
        "document_name": document_name,
        "id": response.get("inventory_adjustment"),
    }
    frappe.enqueue(
        process_request,
        queue="default",
        doctype="Item",
        request_data=request_data,
        route_key="StockAdjustmentTransitionReq",
        handler_function=process_inventory_transition,
        request_method="PATCH",
        settings_name=settings_name,
    )


def process_inventory_transition(response: dict, document_name: str, **kwargs) -> None:
    pass


def sales_information_submission_on_success(response: dict, document_name: str, doctype: str, settings_name: str, **kwargs) -> None:
    """
    Callback after successful submission. Maps SCU data and signature_link.  
    """
    updates = {
        "custom_successfully_submitted": 1,
    }

    frappe.db.set_value(doctype, document_name, updates)

    frappe.enqueue(+
        "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.get_invoice_details",
        document_name=document_name,
        invoice_type=doctype,
        settings_name=settings_name,
        queue="long",
    )
    
    
def sales_information_submission_on_error(response: dict, document_name: str, doctype: str, settings_name: str, **kwargs) -> None:
    from ..overrides.server.sales_invoice import send_invoice_details
    from .apis import perform_item_registration
    doc = frappe.get_doc(doctype, document_name)
    error_message = response if isinstance(response, str) else str(response)
    if "get() returned more than one Product -- it returned 2!" in error_message:
        for item in doc.items:
            perform_item_registration(item.item_code, settings_name)
            
        time.sleep(15)  
        frappe.enqueue(
            send_invoice_details,
            name=doc.name,
            queue='long', timeout=600, now=False, enqueue_after_commit=True,
            at_front=False, job_name=f"retry_invoice_{doc.name}_{int(time.time())}",
        )
        
    elif "get() returned more than one BusinessPartner -- it returned 2!" in error_message:
        from .apis import send_branch_customer_details
        send_branch_customer_details(doc.customer, settings_name)
        time.sleep(15) 
        frappe.enqueue(
            send_invoice_details,
            name=doc.name,
            queue='long', timeout=600, now=False, enqueue_after_commit=True,
            at_front=False, job_name=f"retry_invoice_{doc.name}_{int(time.time())}",
        )
        
# def sales_information_submission_on_success(
#     response: dict, document_name: str, doctype: str, **kwargs
# ) -> None:
#     """
#     Callback function executed after successfully processing an item.
#     Updates the invoice with custom ID and submission status.
#     """
#     frappe.db.set_value(
#         doctype,
#         document_name,
#         {
#             "custom_slade_id": response.get("id"),
#         },
#     )
#     frappe.enqueue(
#         "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.remote_response_status_handlers.process_invoice_items",
#         document_name=document_name,
#         doctype=doctype,
#         invoice_slade_id=response.get("id"),
#         queue="long",
#     )


@frappe.whitelist()
def process_invoice_items(
    document_name: str, doctype: str, invoice_slade_id: str, **kwargs
) -> None:
    """
    Retrieves the specific invoice, extracts all items, and sends each
    item separately.
    """
    from .process_request import process_request

    invoice = frappe.get_doc(doctype, document_name)

    if not invoice:
        frappe.throw(f"{doctype} with name {document_name} not found.")

    items = invoice.get("items", [])
    items_table_doctype = frappe.get_meta(doctype).get_field("items").options
    if not items:
        frappe.throw(f"No items found for {doctype} {document_name}.")

    route_key = "SalesLineSaveReq"
    if invoice.is_return:
        route_key = "SalesCreditNoteLineReq"

    conversion_rate = invoice.conversion_rate or 1

    for item in items:
        tax_amount = item.get("custom_tax_amount", 0) or 0

        converted_tax_amount = round(tax_amount * conversion_rate, 4) if tax_amount else 0

        qty = abs(item.get("qty"))
        base_net_rate = round(item.get("base_net_rate") or 0, 4)
        base_amount = round(abs(item.get("base_amount")) or 0, 4)

        payload = {
            "product": get_link_value(
                "Item", "name", item.get("item_code"), "custom_slade_id"
            ),
            "quantity": round(qty, 4),
            "new_price": round(base_net_rate + (converted_tax_amount / qty if qty else 0), 4),
            "amount": round(base_amount + converted_tax_amount, 4),
            "credit_note" if invoice.is_return else "sales_invoice": invoice_slade_id,
            "document_name": item.get("name"),
            "allow_discount": False,
        }

        request_method = "POST"
        if item.get("custom_slade_id"):
            request_method = "PATCH"
            payload["id"] = item.get("custom_slade_id")

        process_request(
            payload,
            route_key,
            sales_item_submission_on_success,
            doctype=items_table_doctype,
            request_method=request_method,
        )

    process_sales_transition(document_name, doctype, invoice_slade_id)


def process_sales_transition(
    document_name: str, doctype: str, invoice_slade_id: str
) -> None:
    from .process_request import process_request

    invoice = frappe.get_doc(doctype, document_name)

    def handle_transition_success(response: dict, document_name: str, **kwargs) -> None:
        frappe.db.set_value(doctype, document_name, {"custom_transition_successful": 1})
        frappe.enqueue(
            "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.remote_response_status_handlers.process_sales_sign",
            document_name=document_name,
            doctype=doctype,
            invoice_slade_id=response.get("id"),
            queue="long",
        )

    payload = {"invoice_id": invoice_slade_id, "document_name": document_name}
    route_key = "SalesTransitionReq"
    if invoice.is_return:
        route_key = "SalesCreditNoteTransitionReq"

    process_request(
        payload,
        route_key,
        handle_transition_success,
        request_method="PATCH",
        doctype=doctype,
    )


def process_sales_sign(document_name: str, doctype: str, invoice_slade_id: str) -> None:
    from .process_request import process_request

    invoice = frappe.get_doc(doctype, document_name)

    def handle_invoice_sign_success(
        response: dict, document_name: str, **kwargs
    ) -> None:
        frappe.db.set_value(
            doctype, document_name, {"custom_successfully_submitted": 1}
        )
        frappe.enqueue(
            "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.get_invoice_details",
            id=invoice_slade_id,
            document_name=document_name,
            invoice_type=doctype,
            queue="long",
        )

    payload = {"invoice_id": invoice_slade_id, "document_name": document_name}
    route_key = "SalesSignInvReq"
    if invoice.is_return:
        route_key = "SalesCreditNoteSignReq"

    process_request(
        payload,
        route_key,
        handle_invoice_sign_success,
        request_method="POST",
        doctype=doctype,
    )


def update_invoice_info(response: dict, document_name: str, doctype: str, settings_name: str | None = None, **kwargs) -> None:
    doc = frappe.get_doc(doctype, document_name)
    data = response.get("results", [])[0] if response.get("results") else response
    scu_data = data.get("scu_data")
    if not scu_data:
        return

    custom_slade_id = data.get("id")

    invoice_data = build_return_invoice_payload(doc, data) if doc.is_return else build_invoice_payload(doc)
    
    if is_invoice_data_matching(invoice_data, data):
        updates = {
            "custom_slade_id": custom_slade_id,
            **map_scu_fields(scu_data, custom_slade_id, doctype, qr_key="qr_code_url"),
        }

        if document_name:
            frappe.db.set_value(doctype, document_name, updates)
            frappe.publish_realtime("refresh_form", document_name)
            
    elif doc.status != "Credit Note Issued":
        from ..overrides.server.shared_overrides import generic_invoices_on_submit_override
        from .process_request import process_request
        revision_count = int(doc.get("revision_count") or 0) + 1
        allowed_revisions = int(frappe.get_value(
            "Navari eTims Settings", settings_name, "max_allowed_revisions"
        ) if frappe.get_value(
            "Navari eTims Settings", settings_name, "max_allowed_revisions"
        ) else 0)
        
        if allowed_revisions and revision_count > allowed_revisions:
            return
        
        frappe.db.set_value(doctype, document_name, {"revision_count": revision_count})
        new_doc = frappe.get_doc(doctype, document_name)
        generic_invoices_on_submit_override(new_doc, doctype)
        if not new_doc.is_return:
            return_payload = prepare_return_invoice_payload(document_name, data.get("reference_number"), float(data.get("total_gross_amount", 0)), new_doc, data, True)
            frappe.enqueue(
                process_request,
                queue="default",
                is_async=True,
                request_data=return_payload,
                route_key="CreditNoteSaveReq",
                handler_function=sales_information_submission_on_success,
                request_method="POST",
                doctype=doctype,
                settings_name=settings_name,
                company=doc.company,
            )
    

def is_invoice_data_matching(payload: dict, response_data: dict) -> bool:
    """
    Check if the payload invoice data matches the response invoice data.
    Ignores order and allows tolerance for value differences (nearest whole number).
    """
    is_credit_note = 'sales_credit_note_lines' in response_data
    payload_items = payload.get('itemDetails', [])
    response_items = response_data.get('sales_credit_note_lines' if is_credit_note else 'sales_invoice_lines', [])

    if len(payload_items) != len(response_items):
        return False

    payload_total = payload.get('amount') or sum((i.get('unit_price', 0) * i.get('quantity', 0)) for i in payload_items)
    response_total = response_data.get('crn_total_amount') if is_credit_note else response_data.get('total_gross_amount')

    if round(float(payload_total), 0) != round(float(response_total or 0), 0):
        return False

    def normalize(item):
        qty = round(float(item.get('quantity', 0)), 0)  
        price = round(float(item.get('unit_price', 0)), 2)
        total = round(qty * price, 2)
        return (qty, price, total)

    normalized_response = [normalize({
        'quantity': i.get('quantity', 0),
        'unit_price': i.get('price_inclusive_tax', 0)
    }) for i in response_items]

    for p_item in payload_items:
        p_norm = normalize(p_item)
        if p_norm in normalized_response:
            normalized_response.remove(p_norm)  
        else:
            return False  

    return True



def generate_and_attach_qr_code(url: str, docname: str, doctype: str) -> str:
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


def map_scu_fields(data: dict, docname: str, doctype: str, qr_key: str) -> dict:
    qr_url = data.get(qr_key)
    image_url = generate_and_attach_qr_code(qr_url, docname, doctype) if qr_url else None

    return {
        "custom_qr_code_url": qr_url,
        "custom_qr_code": image_url,
        "custom_current_receipt_number": data.get("scu_receipt_number"),
        "custom_control_unit_date_time": parse_datetime(data.get("scu_receipt_timestamp")),
        "custom_receipt_signature": data.get("scu_receipt_signature"),
        "custom_internal_data": data.get("scu_internal_data"),
        "custom_scu_id": data.get("scu_id"),
        "custom_scu_mrc_no": data.get("scu_mrc_number"),
        "custom_scu_invoice_number": data.get("scu_invoice_number"),
    }




def sales_item_submission_on_success(
    response: dict, document_name: str, doctype: str, **kwargs
) -> None:
    updates = {
        "custom_slade_id": response.get("id"),
        "custom_sent_to_slade": 1,
    }
    frappe.db.set_value(doctype, document_name, updates)


def item_composition_submission_on_success(
    response: dict, document_name: str, **kwargs
) -> None:
    from .process_request import process_request

    frappe.db.set_value(
        "BOM",
        document_name,
        {
            "custom_item_composition_submitted_successfully": 1,
            "custom_slade_id": response.get("id"),
        },
    )

    doc = frappe.get_doc("BOM", document_name)

    for item in doc.items:
        request_data = {
            "document_name": item.name,
            "active": True,
            "quantity": item.qty,
            "bom": response.get("id"),
            "raw_product": get_link_value(
                "Item", "name", item.item_code, "custom_slade_id"
            ),
        }
        frappe.enqueue(
            process_request,
            queue="default",
            doctype="BOM Item",
            request_data=request_data,
            route_key="BOMItemReq",
            handler_function=bom_item_submission_on_success,
            request_method="POST",
        )


def bom_item_submission_on_success(
    response: dict, document_name: str, **kwargs
) -> None:
    frappe.db.set_value(
        "BOM Item",
        document_name,
        {
            "custom_slade_id": response.get("id"),
            "custom_submitted_successfully": 1,
        },
    )


def purchase_invoice_submission_on_success(
    response: dict, document_name: str, **kwargs
) -> None:
    frappe.db.set_value(
        "Purchase Invoice",
        document_name,
        {
            "custom_slade_id": response.get("id"),
            "custom_submitted_successfully": 1,
        },
    )


def purchase_search_on_success(response: dict, **kwargs) -> None:
    sales_list = (
        response.get("results", [])
        if isinstance(response, dict)
        else response if isinstance(response, list) else [response]
    )
    for sale in sales_list:
        registered_purchase = create_purchase_from_search_details(sale)
        frappe.enqueue(
            "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.remote_response_status_handlers.fetch_purchase_items",
            registered_purchase=registered_purchase,
            queue="long",
        )


def fetch_purchase_items(registered_purchase: str) -> None:
    from .process_request import process_request

    payload = {
        "purchase_invoice": registered_purchase,
        "document_name": registered_purchase,
    }

    process_request(
        payload,
        "TrnsPurchaseItemReq",
        create_and_link_purchase_item,
        request_method="GET",
        doctype=REGISTERED_PURCHASES_DOCTYPE_NAME,
    )


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


def create_purchase_from_search_details(fetched_purchase: dict) -> str:
    """
    Create and submit a new registered purchase document using details from fetched_purchase.
    """
    existing_doc = frappe.get_value(
        REGISTERED_PURCHASES_DOCTYPE_NAME, {"slade_id": fetched_purchase["id"]}, "name"
    )

    if existing_doc:
        doc = frappe.get_doc(REGISTERED_PURCHASES_DOCTYPE_NAME, existing_doc)
    else:
        doc = frappe.new_doc(REGISTERED_PURCHASES_DOCTYPE_NAME)

    doc.flags.ignore_permissions = True
    doc.flags.ignore_validate_update_after_submit = True
    doc.slade_id = fetched_purchase["id"]

    doc.supplier_name = fetched_purchase["supplier_name"]
    doc.supplier_pin = fetched_purchase["supplier_pin"]
    doc.supplier_branch_id = fetched_purchase["supplier_branch_id"]
    doc.supplier_invoice_number = fetched_purchase["supplier_invoice_number"]

    doc.receipt_type_code = fetched_purchase["receipt_type_code"]
    if fetched_purchase.get("payment_type_code"):
        doc.payment_type_code = frappe.get_doc(
            "Navari KRA eTims Payment Type",
            {"code": fetched_purchase["payment_type_code"]},
            ["name"],
        ).name

    doc.validated_date = parse_datetime(fetched_purchase["validated_date"])
    doc.sales_date = parse_datetime(fetched_purchase["sale_date"])
    doc.stock_released_date = parse_datetime(fetched_purchase["stock_released_date"])
    doc.remarks = fetched_purchase["remark"]

    doc.total_item_count = fetched_purchase["total_item_count"]
    doc.total_taxable_amount = fetched_purchase["total_taxable_amount"]
    doc.total_tax_amount = fetched_purchase["total_tax_amount"]
    doc.total_amount = fetched_purchase["total_amount"]

    doc.taxable_amount_a = fetched_purchase.get("taxable_amount_A", 0.0)
    doc.taxable_amount_b = fetched_purchase.get("taxable_amount_B", 0.0)
    doc.taxable_amount_c = fetched_purchase.get("taxable_amount_C", 0.0)
    doc.taxable_amount_d = fetched_purchase.get("taxable_amount_D", 0.0)
    doc.taxable_amount_e = fetched_purchase.get("taxable_amount_E", 0.0)

    doc.tax_rate_a = fetched_purchase.get("tax_rate_A", 0.0)
    doc.tax_rate_b = fetched_purchase.get("tax_rate_B", 0.0)
    doc.tax_rate_c = fetched_purchase.get("tax_rate_C", 0.0)
    doc.tax_rate_d = fetched_purchase.get("tax_rate_D", 0.0)
    doc.tax_rate_e = fetched_purchase.get("tax_rate_E", 0.0)

    doc.tax_amount_a = fetched_purchase.get("tax_amount_A", 0.0)
    doc.tax_amount_b = fetched_purchase.get("tax_amount_B", 0.0)
    doc.tax_amount_c = fetched_purchase.get("tax_amount_C", 0.0)
    doc.tax_amount_d = fetched_purchase.get("tax_amount_D", 0.0)
    doc.tax_amount_e = fetched_purchase.get("tax_amount_E", 0.0)

    doc.workflow_state = fetched_purchase["workflow_state"]
    doc.branch = (get_link_value("Branch", "slade_id", fetched_purchase["branch"]),)
    doc.organisation = (
        get_link_value("Company", "custom_slade_id", fetched_purchase["organisation"]),
    )
    doc.can_send_to_etims = fetched_purchase["can_send_to_etims"]

    try:
        doc.submit()
    except frappe.exceptions.DuplicateEntryError:
        frappe.log_error(
            title="Duplicate entries", message=f"Duplicate for document: {doc.name}"
        )

    return doc.name


def create_and_link_purchase_item(response: dict, document_name: str, **kwargs) -> None:
    item_list = response if isinstance(response, list) else response.get("results")
    parent_record = frappe.get_doc(REGISTERED_PURCHASES_DOCTYPE_NAME, document_name)
    parent_record.flags.ignore_permissions = True
    parent_record.flags.ignore_validate_update_after_submit = True

    for item in item_list:
        existing_item = frappe.get_all(
            REGISTERED_PURCHASES_DOCTYPE_NAME_ITEM, filters={"slade_id": item["id"]}
        )

        if existing_item:
            registered_item = frappe.get_doc(
                REGISTERED_PURCHASES_DOCTYPE_NAME_ITEM, item["id"]
            )
            registered_item.flags.ignore_permissions = True
            registered_item.flags.ignore_validate_update_after_submit = True
        else:
            registered_item = frappe.new_doc(REGISTERED_PURCHASES_DOCTYPE_NAME_ITEM)
            registered_item.parent = parent_record.name
            registered_item.parentfield = "items"
            registered_item.parenttype = REGISTERED_PURCHASES_DOCTYPE_NAME

        registered_item.slade_id = item["id"]
        registered_item.item_name = item["item_name"]
        registered_item.purchase_invoice = item["purchase_invoice"]
        registered_item.is_mapped = 1 if item["is_mapped"] else 0
        registered_item.product_name = item["product_name"]
        registered_item.product_code = item["product_code"]
        registered_item.item_code = item["item_code"]
        registered_item.item_classification_code_data = item["item_classification_code"]
        registered_item.item_classification_code = get_or_create_link(
            ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
            "itemclscd",
            item["item_classification_code"],
        )
        registered_item.item_sequence = item["item_sequence_number"]
        registered_item.barcode = item["barcode"]
        registered_item.package = item["package"]
        registered_item.packaging_unit_code = item["package_unit_code"]
        registered_item.quantity = item["quantity"]
        registered_item.quantity_unit_code = item["quantity_unit_code"]
        registered_item.unit_price = item["unit_price"]
        registered_item.supply_amount = item["supply_amount"]
        registered_item.discount_rate = item["discount_rate"]
        registered_item.discount_amount = item["discount_amount"]
        registered_item.taxation_type_code = item["taxation_type_code"]
        registered_item.taxable_amount = item["taxable_amount"]
        registered_item.tax_amount = item["tax_amount"]
        registered_item.total_amount = item["total_amount"]
        registered_item.save(ignore_permissions=True)

        parent_record.append("items", registered_item)

    parent_record.save(ignore_permissions=True)


def notices_search_on_success(response: dict | list, **kwargs) -> None:
    notices = response if isinstance(response, list) else response.get("results")
    if isinstance(notices, list):
        for notice in notices:
            create_notice_if_new(notice)
    else:
        frappe.log_error(
            title="Invalid Response Format",
            message="Expected a list or single notice in the response",
        )


def create_notice_if_new(notice: dict) -> None:
    exists = frappe.db.exists(
        NOTICES_DOCTYPE_NAME, {"notice_number": notice.get("notice_number")}
    )
    if exists:
        return

    doc = frappe.new_doc(NOTICES_DOCTYPE_NAME)
    doc.flags.ignore_permissions = True
    doc.flags.ignore_validate_update_after_submit = True
    doc.update(
        {
            "notice_number": notice.get("notice_number"),
            "title": notice.get("title"),
            "registration_name": notice.get("registration_name"),
            "details_url": notice.get("detail_url"),
            "registration_datetime": datetime.fromisoformat(
                notice.get("registration_date")
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "contents": notice.get("content"),
        }
    )
    doc.save(ignore_permissions=True)

    try:
        doc.submit()
    except frappe.exceptions.DuplicateEntryError:
        frappe.log_error(
            title="Duplicate Entry Error",
            message=f"Duplicate notice detected: {notice.get('notice_number')}",
        )
    except Exception as e:
        frappe.log_error(
            title="Notice Creation Failed",
            message=f"Error creating notice {notice.get('notice_number')}: {str(e)}",
        )


def imported_items_search_on_success(response: dict,  settings_name: str, **kwargs) -> None:
    items = response.get("results", [])
    batch_size = 20
    counter = 0

    for item in items:
        try:
            item_id = item.get("id")
            existing_item = frappe.db.get_value(
                REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME,
                {"slade_id": item_id},
                "name",
                order_by="creation desc",
            )

            request_data = {
                "item_name": item.get("item_name"),
                "product_name": item.get("product_name"),
                "product_code": item.get("product_code"),
                "task_code": item.get("task_code"),
                "declaration_date": parse_date(item.get("declaration_date")),
                "item_sequence": item.get("item_sequence"),
                "declaration_number": item.get("declaration_number"),
                "imported_item_status_code": item.get("import_item_status_code"),
                "imported_item_status": get_link_value(
                    IMPORTED_ITEMS_STATUS_DOCTYPE_NAME,
                    "code",
                    item.get("import_item_status_code"),
                ),
                "hs_code": item.get("hs_code"),
                "origin_nation_code": get_link_value(
                    COUNTRIES_DOCTYPE_NAME, "code", item.get("origin_nation_code")
                ),
                "export_nation_code": get_link_value(
                    COUNTRIES_DOCTYPE_NAME, "code", item.get("export_nation_code")
                ),
                "package": item.get("package"),
                "packaging_unit_code": get_or_create_link(
                    PACKAGING_UNIT_DOCTYPE_NAME, "code", item.get("packaging_unit_code")
                ),
                "quantity": item.get("quantity"),
                "quantity_unit_code": get_or_create_link(
                    UNIT_OF_QUANTITY_DOCTYPE_NAME,
                    "code",
                    item.get("quantity_unit_code"),
                ),
                "branch": get_or_create_link("Branch", "slade_id", item.get("branch")),
                # "organisation": get_or_create_link(
                #     ORGANISATION_UNIT_DOCTYPE_NAME, "slade_id", item.get("organisation")
                # ),
                "gross_weight": item.get("gross_weight"),
                "net_weight": item.get("net_weight"),
                "suppliers_name": item.get("supplier_name"),
                "agent_name": item.get("agent_name"),
                "invoice_foreign_currency_amount": item.get(
                    "invoice_foreign_currency_amount"
                ),
                "invoice_foreign_currency": item.get("invoice_foreign_currency_code"),
                "invoice_foreign_currency_rate": item.get(
                    "invoice_foreign_currency_exchange"
                ),
                "slade_id": item_id,
                "sent_to_etims": 1 if item.get("sent_to_etims") else 0,
            }

            if existing_item:
                item_doc = frappe.get_doc(
                    REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME, existing_item
                )
                item_doc.update(request_data)
                item_doc.flags.ignore_mandatory = True
                item_doc.save(ignore_permissions=True)
            else:
                item_doc = frappe.get_doc(
                    {"doctype": REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME, **request_data}
                )
                item_doc.insert(
                    ignore_permissions=True,
                    ignore_mandatory=True,
                    ignore_if_duplicate=True,
                )

            if item.get("product"):
                product_name = None
                if frappe.db.exists(SLADE_ID_MAPPING_DOCTYPE_NAME, {"slade360_id": item.get("product"), "etims_setup": settings_name}):
                    product_name = frappe.db.get_value(
                        SLADE_ID_MAPPING_DOCTYPE_NAME, 
                        {"slade360_id": item.get("product"), "etims_setup": settings_name}, 
                        "parent", 
                        order_by="creation desc"
                    )
                    product = frappe.get_doc("Item", product_name)
                else:
                    item_name = item.get("item_name") or item.get("product_name", "Imported Item")
                    product_code = item.get("product_code") or item_name
                    
                    if product_code and frappe.db.exists("Item", {"item_code": product_code}):
                        product = frappe.get_doc("Item", {"item_code": product_code})
                    else:
                        product = frappe.new_doc("Item")
                        product.item_name = item_name
                        product.item_code = product_code or item_name
                        default_item_group = frappe.get_all("Item Group", filters={"is_group": 1}, fields=["name"], limit=1)
                        product.item_group = default_item_group[0].name if default_item_group else "All Item Groups"
                        product.flags.ignore_mandatory = True
                        product.insert(ignore_permissions=True)
                    frappe.get_doc(
                        {
                            "doctype": SLADE_ID_MAPPING_DOCTYPE_NAME,
                            "parent": product.name,
                            "parenttype": "Item",
                            "parentfield": "etims_setup_mapping",
                            "slade360_id": item.get("product"),
                            "etims_setup": settings_name,
                        }
                    ).insert(ignore_permissions=True)
                
                update_data = {
                    "custom_referenced_imported_item": item_doc.name,
                    "custom_imported_item_task_code": item_doc.task_code,
                    "custom_hs_code": item_doc.hs_code,
                    "custom_imported_item_submitted": item_doc.sent_to_etims,
                    "custom_imported_item_status": item_doc.imported_item_status,
                    "custom_imported_item_status_code": item_doc.imported_item_status_code
                }
                frappe.db.set_value("Item", product.name, update_data, update_modified=False)

            counter += 1
            if counter % batch_size == 0:
                frappe.db.commit()

        except Exception as e:
            raise e

    if counter % batch_size != 0:
        frappe.db.commit()

    frappe.msgprint(
        "Imported Items fetched successfully. Go to <b>Navari eTims Registered Imported Item</b> Doctype for more information."
    )


def parse_date(date_str: str) -> None:
    formats = [
        "%d%m%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y.%m.%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    if date_str.isdigit():
        try:
            return datetime.fromtimestamp(int(date_str))
        except ValueError:
            pass
    raise ValueError(f"Invalid date format: {date_str}")


def search_branch_request_on_success(response: dict, **kwargs) -> None:
    for branch in response.get("results", []):
        doc = None

        try:
            doc = frappe.get_doc(
                "Branch",
                {"slade_id": branch["id"]},
                for_update=True,
            )

        except frappe.exceptions.DoesNotExistError:
            doc = frappe.new_doc("Branch")

        finally:
            doc.branch = branch["name"]
            doc.slade_id = branch["id"]
            doc.custom_etims_device_serial_no = branch["etims_device_serial_no"]
            doc.custom_branch_code = branch["etims_branch_id"]
            doc.custom_pin = branch["organisation_tax_pin"]
            doc.custom_branch_name = branch["name"]
            doc.custom_branch_status_code = branch["branch_status"]
            doc.custom_county_name = branch["county_name"]
            doc.custom_sub_county_name = branch["sub_county_name"]
            doc.custom_tax_locality_name = branch["tax_locality_name"]
            doc.custom_location_description = branch["location_description"]
            doc.custom_manager_name = branch["manager_name"]
            doc.custom_manager_contact = branch["parent_phone_number"]
            doc.custom_manager_email = branch["email_address"]
            doc.custom_is_head_office = "Y" if branch["is_headquater"] else "N"
            doc.custom_is_etims_branch = 1 if branch["is_etims_verified"] else 0

            doc.save(ignore_permissions=True)


def item_search_on_success(response: dict, settings_name : str, **kwargs) -> None:
    items = parse_response_data(response, list)
    for item_data in items:
        try:
            slade_id = item_data.get("id")
            existing_item = frappe.db.get_value(
                SLADE_ID_MAPPING_DOCTYPE_NAME, {"slade360_id": slade_id, "etims_setup": settings_name}, "parent", order_by="creation desc"
            )
            country_of_origin_code = item_data.get("country_of_origin")[
                :2
            ] if item_data.get("country_of_origin") else "ke"
            country_of_origin = get_link_value(
                COUNTRIES_DOCTYPE_NAME, "code", country_of_origin_code
            )

            if existing_item:
                item_doc = frappe.get_doc("Item", existing_item)

            request_data = {
                "item_name": item_data.get("description", "Unknown Item"),
                "item_code": item_data.get("code", ""),
                "description": item_data.get("description", ""),
                "is_sales_item": item_data.get("can_be_sold", False),
                "is_purchase_item": item_data.get("can_be_purchased", False),
                "custom_item_code_etims": item_data.get("scu_item_code", ""),
                "custom_etims_country_of_origin_code": country_of_origin_code or "",
                "valuation_rate": round(item_data.get("selling_price", 0.0), 2),
                "last_purchase_rate": round(item_data.get("purchasing_price", 0.0), 2),
                "custom_etims_country_of_origin": country_of_origin or "",
                "custom_item_type": item_data.get("item_type", ""),
                "custom_product_type": item_data.get("product_type", ""),
            }

            if item_data.get("scu_item_classification"):
                request_data["custom_item_classification"] = get_parent_by_slade360_id(
                    ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
                    item_data.get("scu_item_classification"),
                    settings_name,
                )

            if item_data.get("packaging_unit"):
                request_data["custom_packaging_unit"] = get_parent_by_slade360_id(
                    PACKAGING_UNIT_DOCTYPE_NAME,
                    item_data.get("packaging_unit"),
                    settings_name,
                )

            if item_data.get("quantity_unit"):
                request_data["custom_unit_of_quantity"] = get_parent_by_slade360_id(
                    UNIT_OF_QUANTITY_DOCTYPE_NAME,
                    item_data.get("quantity_unit"),
                    settings_name,
                )

            if item_data.get("sale_taxes") and len(item_data.get("sale_taxes", [])) > 0:
                request_data["custom_taxation_type"] = get_parent_by_slade360_id(
                    TAXATION_TYPE_DOCTYPE_NAME,
                    item_data.get("sale_taxes")[0],
                    settings_name,
                )

            if existing_item:
                item_doc = frappe.get_doc("Item", existing_item)
                item_doc.update(request_data)
                item_doc.flags.ignore_mandatory = True
                item_doc.save(ignore_permissions=True)
            else:
                request_data["item_group"] = frappe.db.get_value("Item Group", {"is_group": 1}, "name") or "All Item Groups"
                item_doc = frappe.get_doc({"doctype": "Item", **request_data})
                item_doc.flags.ignore_mandatory = True
                item_doc.insert(
                    ignore_permissions=True,
                    ignore_mandatory=True,
                    ignore_if_duplicate=True,
                )
                
            existing_mapping = frappe.db.exists(SLADE_ID_MAPPING_DOCTYPE_NAME, {
                "parent": item_doc.name,
                "parenttype": "Item",
                "parentfield": "etims_setup_mapping",
                "etims_setup": settings_name
            })
            
            if existing_mapping:
                frappe.db.set_value(
                    SLADE_ID_MAPPING_DOCTYPE_NAME,
                    existing_mapping,
                    {"slade360_id": slade_id}
                )
            else:
                frappe.get_doc({
                    "doctype": SLADE_ID_MAPPING_DOCTYPE_NAME,
                    "parent": item_doc.name,
                    "parenttype": "Item",
                    "parentfield": "etims_setup_mapping",
                    "slade360_id": slade_id,
                    "etims_setup": settings_name,
                }).insert(ignore_permissions=True)

        except Exception as e:
            frappe.log_error(
                title="Item Search Error",
                message=f"Error processing item {item_data.get('code')}: {str(e)}",
            )
            continue
        
    frappe.db.commit()


def initialize_device_submission_on_success(response: dict, **kwargs) -> None:
    pass


def customers_search_on_success(response: dict, **kwargs) -> None:
    data = response.get("results", []) if response.get("results") else response
    if isinstance(data, dict):
        data = [data]
    for customer in data:
        existing_customer = frappe.db.exists("Customer", {"slade_id": customer["id"]})
        data = {
            "email_id": customer["email_address"],
            "mobile_no": customer["phone_number"],
            "tax_id": customer.get("customer_tax_pin"),
            "currency": customer.get("currency"),
            "active": 1 if customer.get("active") else 0,
            "customer_type": (
                customer.get("customer_type").title()
                if customer.get("customer_type")
                in ["Company", "Individual", "Partnership"]
                else "Individual"
            ),
        }

        if existing_customer and customer.get("is_customer"):
            doc = frappe.get_doc("Customer", existing_customer)
            doc.update(data)
            doc.save(ignore_permissions=True)
        else:
            doc = frappe.new_doc("Customer")
            doc.update(data)
            doc.insert(ignore_permissions=True)
        frappe.db.commit()


def location_update_on_success(response: dict, document_name: str, **kwargs) -> None:
    frappe.db.set_value(
        "Warehouse", document_name, {"custom_slade_id": response.get("id")}
    )


def pricelist_update_on_success(response: dict, document_name: str, **kwargs) -> None:
    frappe.db.set_value(
        "Price List", document_name, {"custom_slade_id": response.get("id")}
    )


def item_price_update_on_success(response: dict, document_name: str, **kwargs) -> None:
    frappe.db.set_value(
        "Item Price", document_name, {"custom_slade_id": response.get("id")}
    )


def operation_type_create_on_success(
    response: dict, document_name: str, **kwargs
) -> None:
    frappe.db.set_value(
        OPERATION_TYPE_DOCTYPE_NAME, document_name, {"slade_id": response.get("id")}
    )


def mode_of_payment_on_success(response: dict, document_name: str, settings_name: str, **kwargs) -> None:
    # Get the Mode of Payment document
    mop_doc = frappe.get_doc("Mode of Payment", document_name)
    slade_id = response.get("id")
    
    # Find existing mapping or create new one
    for mapping in mop_doc.get("etims_setup_mapping", []):
        if mapping.etims_setup == settings_name:
            mapping.slade360_id = slade_id
            break
    else:
        mop_doc.append("etims_setup_mapping", {
            "etims_setup": settings_name,
            "slade360_id": slade_id
        })
        
    mop_doc.save(ignore_permissions=True)


def fetch_matching_items_on_success(response: dict, document_name: str, settings_name: str, **kwargs) -> None:
    from .process_request import process_request
    items = parse_response_data(response, list)
    item_doc = frappe.get_doc("Item", document_name)
    existing_id = next((row.slade360_id for row in item_doc.etims_setup_mapping if row.etims_setup == settings_name), None)
    if len(items) > 0:
        if not item_doc.etims_setup_mapping:
            frappe.get_doc({
                "doctype": SLADE_ID_MAPPING_DOCTYPE_NAME,
                "parent": item_doc.name,
                "parenttype": "Item",
                "parentfield": "etims_setup_mapping",
                "slade360_id": items[0].get("id"),
                "etims_setup": settings_name
            }).insert(ignore_permissions=True)
            
            existing_id = items[0].get("id")

        for item in items:
            if existing_id != item.get("id"):
                frappe.enqueue(
                    process_request,
                    queue="default",
                    doctype="Item",
                    request_data={
                        "document_name": item_doc.name,
                        "name": f"{item_doc.name} - Archived",
                        "id": item.get("id"),
                        "active": False, 
                    },
                    route_key="ItemsSearchReq",
                    handler_function=item_archive_on_success,
                    request_method="PATCH",
                    settings_name=settings_name,
                )
    request_data = build_item_payload(item_doc, settings_name, existing_id)
    request_method = "PATCH" if "id" in request_data else "POST"        
    frappe.enqueue(
        process_request,
        queue="default",
        doctype="Item",
        request_data=request_data,
        route_key="ItemsSearchReq",
        handler_function=item_registration_on_success,
        request_method=request_method,
        settings_name=settings_name,
    )

def item_archive_on_success(response: dict, document_name: str, **kwargs) -> None:
    pass


def fetch_matching_partner_on_success(response: dict, doctype: str, document_name: str, settings_name: str, **kwargs) -> None:
    from .process_request import process_request
    partners = parse_response_data(response, list)
    partner_doc = frappe.get_doc(doctype, document_name)
    existing_id = next((row.slade360_id for row in partner_doc.etims_setup_mapping if row.etims_setup == settings_name), None)
    if len(partners) > 0:
        if not partner_doc.etims_setup_mapping:
            frappe.get_doc({
                "doctype": SLADE_ID_MAPPING_DOCTYPE_NAME,
                "parent": partner_doc.name,
                "parenttype": doctype,
                "parentfield": "etims_setup_mapping",
                "slade360_id": partners[0].get("id"),
                "etims_setup": settings_name
            }).insert(ignore_permissions=True)
            
            existing_id = partners[0].get("id")

        for partner in partners:
            if existing_id != partner.get("id"):
                frappe.enqueue(
                    process_request,
                    queue="default",
                    doctype=doctype,
                    request_data={
                        "document_name": partner_doc.name,
                        "partner_name": f"{partner_doc.name} - Archived",
                        "customer_tax_pin": None,
                        "id": partner.get("id"),
                        "active": False, 
                    },
                    route_key="BhfCustSaveReq",
                    handler_function=partner_archive_on_success,
                    request_method="PATCH",
                    settings_name=settings_name,
                )
    request_data = build_partner_payload(partner_doc, settings_name, is_customer=(doctype == "Customer"), existing_id=existing_id)
    request_method = "PATCH" if "id" in request_data else "POST"        
    frappe.enqueue(
        process_request,
        queue="default",
        doctype=doctype,
        request_data=request_data,
        route_key="BhfCustSaveReq",
        handler_function=customer_branch_details_submission_on_success,
        request_method=request_method,
        settings_name=settings_name,
    )

def partner_archive_on_success(response: dict, document_name: str, **kwargs) -> None:
    pass
