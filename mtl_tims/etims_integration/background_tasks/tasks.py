import json
from datetime import datetime, timedelta

import frappe
import frappe.defaults
from frappe.model.document import Document
from frappe import _
from typing import List

from ..apis.api_builder import EndpointsBuilder
from ..apis.process_request import process_request
from ..apis.remote_response_status_handlers import notices_search_on_success
from ..doctype.doctype_names_mapping import (
    OPERATION_TYPE_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
    UOM_CATEGORY_DOCTYPE_NAME,
    WORKSTATION_DOCTYPE_NAME,
)
from ..utils import get_settings, get_max_submission_attempts
from .task_response_handlers import (
    itemprice_search_on_success,
    operation_types_search_on_success,
    pricelist_search_on_success,
    uom_category_search_on_success,
    uom_search_on_success,
    update_branches,
    update_clusters,
    update_countries,
    update_currencies,
    update_item_classification_codes,
    update_packaging_units,
    update_taxation_type,
    update_unit_of_quantity,
    update_workstations,
    warehouse_search_on_success,
)

endpoints_builder = EndpointsBuilder()


def refresh_notices() -> None:
    setups = frappe.get_all(
        SETTINGS_DOCTYPE_NAME,
        filters={"is_active": 1, "sandbox": 0},
        fields=["name"],
    )
    for setup in setups:
        settings_name = setup.name
        if not settings_name:
            continue
        try:
            perform_notice_search({}, settings_name)
        except Exception as e:
            frappe.log_error(
                f"Error performing notice search for {settings_name}: {str(e)}"
            )
            continue


def get_timeframe() -> timedelta:
    settings = get_settings()
    timeframe = settings.get("sales_information_submission_timeframe", 86400) or 86400
    return timedelta(seconds=timeframe)


def fetch_sales_invoices(filters: dict) -> list:
    return frappe.get_all("Sales Invoice", filters, ["name"])


def send_sales_invoices_information() -> None:
    settings = get_settings()
    if not settings.get("sales_auto_submission_enabled"):
        return
    timeframe_ago = datetime.now() - get_timeframe()
    all_submitted_unsent = fetch_sales_invoices(
        {
            "docstatus": 1,
            "custom_successfully_submitted": 0,
            "creation": [">=", timeframe_ago],
        }
    )
    if all_submitted_unsent:
        submit_new_invoices(all_submitted_unsent)

    successful_without_scu_data = fetch_sales_invoices(
        {
            "docstatus": 1,
            "custom_successfully_submitted": 1,
            "custom_qr_code": ["is", "not set"],
            "creation": [">=", timeframe_ago],
        }
    )
    if successful_without_scu_data:
        fetch_scu_data(successful_without_scu_data)

    # sent_unprocessed = fetch_sales_invoices(
    #     {
    #         "docstatus": 1,
    #         "custom_slade_id": ["is", "set"],
    #         "custom_successfully_submitted": 0,
    #         "custom_transition_successful": 0,
    #         "creation": [">=", timeframe_ago],
    #     }
    # )
    # if sent_unprocessed:
    #     process_sent_invoices(sent_unprocessed)

    # processed_unsent_to_etims = fetch_sales_invoices(
    #     {
    #         "docstatus": 1,
    #         "custom_successfully_submitted": 0,
    #         "custom_transition_successful": 1,
    #         "creation": [">=", timeframe_ago],
    #         "is_opening":"No"
    #     }
    # )
    # if processed_unsent_to_etims:
    #     sign_processed_invoices(processed_unsent_to_etims)


def handle_invoice_submission(invoices: list, action_func: callable) -> None:
    max_tries = get_max_submission_attempts()

    for sales_invoice in invoices:
        doc = frappe.get_doc("Sales Invoice", sales_invoice.name, for_update=False)
        tries = int(doc.custom_submission_attempts or 0)

        if tries >= max_tries:
            continue

        try:
            action_func(doc)
            frappe.db.set_value(
                "Sales Invoice",
                sales_invoice.name,
                "custom_submission_attempts",
                tries + 1,
            )
        except Exception as e:
            frappe.log_error(f"Error processing invoice {sales_invoice.name}: {str(e)}")
            frappe.db.set_value(
                "Sales Invoice",
                sales_invoice.name,
                "custom_submission_attempts",
                tries + 1,
            )
            continue


def submit_new_invoices(invoices: list) -> None:
    from ..overrides.server.sales_invoice import on_submit

    def action_func(doc: Document) -> None:
        on_submit(doc)

    handle_invoice_submission(invoices, action_func)


def sign_processed_invoices(invoices: list) -> None:
    from ..apis.remote_response_status_handlers import process_sales_sign

    def action_func(doc: Document) -> None:
        process_sales_sign(doc.name, "Sales Invoice", doc.custom_slade_id)

    handle_invoice_submission(invoices, action_func)


def process_sent_invoices(invoices: list) -> None:
    from ..apis.remote_response_status_handlers import process_invoice_items

    def action_func(doc: Document) -> None:
        process_invoice_items(doc.name, "Sales Invoice", doc.custom_slade_id)

    handle_invoice_submission(invoices, action_func)


def fetch_scu_data(invoices: list) -> None:
    from ..apis.apis import get_invoice_details

    for sales_invoice in invoices:
        try:
            doc = frappe.get_doc("Sales Invoice", sales_invoice.name, for_update=False)
            tries = int(doc.custom_submission_attempts or 0)
            max_tries = get_max_submission_attempts()
            if tries >= max_tries:
                continue
            get_invoice_details(id=doc.custom_slade_id, document_name=doc.name)
            frappe.db.set_value(
                "Sales Invoice",
                sales_invoice.name,
                "custom_submission_attempts",
                tries + 1,
            )
        except Exception as e:
            frappe.log_error(
                f"Error fetching SCU data for invoice {sales_invoice.name}: {str(e)}"
            )
            frappe.db.set_value(
                "Sales Invoice",
                sales_invoice.name,
                "custom_submission_attempts",
                tries + 1,
            )
            continue


@frappe.whitelist()
def perform_notice_search(request_data: str, settings_name: str)  -> str:
    """Function to perform notice search."""
    message = process_request(
        request_data, "NoticeSearchReq", notices_search_on_success, settings_name=settings_name
    )
    return message


@frappe.whitelist()
def refresh_code_lists(request_data: str, settings_name: str) -> str:
    """Refresh code lists based on request data."""
    tasks = [
        ("CurrencyCountrySearchReq", update_countries),
        ("CurrencySearchReq", update_currencies),
        ("PackagingUnitSearchReq", update_packaging_units),
        ("QuantityUnitsSearchReq", update_unit_of_quantity),
        ("TaxSearchReq", update_taxation_type),
    ]

    messages = [process_request(request_data, task[0], task[1], settings_name=settings_name) for task in tasks]

    return messages


@frappe.whitelist()
def search_organisations_request(request_data: str | dict, settings_name: str) -> str:
    """Refresh code lists based on request data."""
    tasks = [
        # ("OrgSearchReq", update_organisations), # Shift to the auth API 
        # ("ClusterSearchReq", update_clusters),
        ("BhfSearchReq", update_branches),
        # ("DeptSearchReq", update_departments),  # Shift to the auth API
        ("WorkstationSearchReq", update_workstations),
    ]

    messages = [process_request(request_data, task[0], task[1], settings_name=settings_name) for task in tasks]
    
    process_request(
        {"location_type": "internal"},
        "LocationsSearchReq",
        warehouse_search_on_success,
        doctype="Warehouse",
        settings_name=settings_name,
    )

    return messages

@frappe.whitelist()
def search_clusters(request_data: str | dict, settings_name: str) -> str:
    """Search clusters and return data for modal matching"""
    if isinstance(request_data, str):
        try:
            request_data = json.loads(request_data)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON string: {request_data}")

    response = process_request(
        request_data,
        "ClusterSearchReq",
        update_clusters,
        settings_name=settings_name,
        doctype=SETTINGS_DOCTYPE_NAME,
    )
    
    return get_cluster_company_matches(response if isinstance(response, list) else response.get("results", [response]))

@frappe.whitelist()
def get_cluster_company_matches(cluster_data):
    """Process cluster data and attempt to match with companies"""
    try:
        if isinstance(cluster_data, str):
            cluster_data = json.loads(cluster_data)

        companies = frappe.get_all("Company", pluck="name")

        matched_data = []
        
        for cluster in cluster_data:
            if not isinstance(cluster, dict):
                continue

            match_info = {
                "cluster_id": cluster.get("id"),
                "cluster_name": cluster.get("name"),
                "organisation": cluster.get("organisation"),
                "company": find_best_company_match(cluster.get("name"), companies)
            }
            
            matched_data.append(match_info)

        return matched_data

    except Exception as e:
        frappe.log_error(f"Cluster matching failed: {str(e)}")
        return {"error": str(e)}

def find_best_company_match(cluster_name, companies):
    """Simple company matching using string comparison"""
    if not cluster_name or not companies:
        return ""
    
    cluster_lower = cluster_name.lower()
    
    for company in companies:
        if company.lower() == cluster_lower:
            return company
    
    for company in companies:
        company_lower = company.lower()
        if cluster_lower in company_lower or company_lower in cluster_lower:
            return company
    
    cluster_words = get_significant_words(cluster_lower)
    if cluster_words:
        for company in companies:
            company_words = get_significant_words(company.lower())
            if any(word in company_words for word in cluster_words):
                return company
    
    return ""

def get_significant_words(text):
    """Extract meaningful words for matching"""
    common_words = {'the', 'and', 'of', 'for', 'in', 'with', 'company', 'co', 'ltd', 'pty'}
    return [
        word for word in text.split() 
        if len(word) > 3 and word not in common_words
    ]

@frappe.whitelist()
def get_item_classification_codes(request_data: str, settings_name: str) -> str:
    """Function to get item classification codes."""
    message = process_request(
        request_data, "ItemClsSearchReq", update_item_classification_codes, settings_name=settings_name
    )
    return message


@frappe.whitelist()
def fetch_etims_uom_categories(request_data: str) -> None:
    message = process_request(
        request_data,
        "UOMCategoriesSearchReq",
        uom_category_search_on_success,
        doctype=UOM_CATEGORY_DOCTYPE_NAME,
    )
    return message


@frappe.whitelist()
def fetch_etims_uom_list(request_data: str) -> None:
    message = process_request(
        request_data,
        "UOMListSearchReq",
        uom_search_on_success,
        doctype="UOM",
    )
    return message


@frappe.whitelist()
def fetch_etims_pricelists(request_data: str) -> None:
    pricelists = process_request(
        request_data,
        "PriceListsSearchReq",
        pricelist_search_on_success,
        doctype="Price List",
    )
    return pricelists


@frappe.whitelist()
def fetch_etims_item_prices(request_data: str) -> None:
    itemprices = process_request(
        request_data,
        "ItemPricesSearchReq",
        itemprice_search_on_success,
        doctype="Item Price",
    )
    return itemprices


@frappe.whitelist()
def fetch_etims_operation_types(request_data: str) -> None:
    operation_types = process_request(
        request_data,
        "OperationTypesReq",
        operation_types_search_on_success,
        doctype=OPERATION_TYPE_DOCTYPE_NAME,
    )
    return operation_types


def send_stock_information() -> None:
    from ..overrides.server.stock_ledger_entry import fetch_current_stock_balance
    settings = get_settings()
    if not settings.get("stock_auto_submission_enabled"):
        return

    timeframe = settings.get("stock_information_submission_timeframe", 86400) or 86400
    duration = timedelta(seconds=timeframe)
    timeframe_ago = datetime.now() - duration
    entries = fetch_stock_ledgers(timeframe_ago)  
    max_tries = get_max_submission_attempts("Stock Ledger Entry")
    for entry in entries:
        if int(entry.custom_submission_tries) >= max_tries:
            continue
        fetch_current_stock_balance(entry) 
         

def fetch_stock_ledgers(timeframe_ago: datetime) -> List[Document]:
    max_tries = get_max_submission_attempts("Stock Ledger Entry")
    entries = frappe.get_all(
        "Stock Ledger Entry",
        filters={
            "docstatus": 1,
            "custom_submitted_successfully": 0,
            "creation": [">=", timeframe_ago],
            "custom_submission_tries": ["<", max_tries],
        },
        fields=["name", "item_code"],
        order_by="creation asc",  
    )

    seen_items = set()
    oldest_entries = []
    for entry in entries:
        if entry["item_code"] not in seen_items:
            seen_items.add(entry["item_code"])
            oldest_entries.append(entry)

    return [frappe.get_doc("Stock Ledger Entry", entry["name"]) for entry in oldest_entries]


def send_purchase_information() -> None:
    from ..overrides.server.purchase_invoice import on_submit

    settings = get_settings()
    if not settings.get("purchase_auto_submission_enabled"):
        return
    timeframe = (
        settings.get("purchase_information_submission_timeframe", 86400) or 86400
    )
    duration = timedelta(seconds=timeframe)
    timeframe_ago = datetime.now() - duration
    all_submitted_purchase_invoices: list[Document] = frappe.get_all(
        "Purchase Invoice",
        {
            "docstatus": 1,
            "custom_submitted_successfully": 0,
            "creation": [">=", timeframe_ago],
        },
        ["name"],
    )

    for invoice in all_submitted_purchase_invoices:
        doc = frappe.get_doc(
            "Purchase Invoice", invoice.name, for_update=False
        ) 

        try:
            frappe.enqueue(on_submit, doc=doc)

        except TypeError:
            continue
      
        
@frappe.whitelist() 
def update_setting_passwords() -> None:
    settings_list = frappe.get_all(
        "Navari KRA ETIMS Settings",
        filters={"is_active": 1, "sandbox": 0},
        fields=["name"]
    )
    for setting in settings_list:
        doc = frappe.get_doc("Navari KRA ETIMS Settings", setting.name)
        doc.update_password()


@frappe.whitelist()
def fetch_workstations(settings_name: str) -> None:
    itemprices = process_request(
        {},
        "WorkstationSearchReq",
        update_workstations,
        doctype=WORKSTATION_DOCTYPE_NAME,
        settings_name=settings_name,
    )
    return itemprices


@frappe.whitelist()
def search_branch_request(request_data: str | dict, settings_name: str) -> None:
    return process_request(
        request_data, "BhfSearchReq", update_branches, doctype="Branch", settings_name=settings_name
    )
