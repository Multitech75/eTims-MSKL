import json

import frappe
import frappe.defaults
from frappe.model.document import Document

from ..doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
    ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
    OPERATION_TYPE_DOCTYPE_NAME,
    PACKAGING_UNIT_DOCTYPE_NAME,
    PAYMENT_TYPE_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
    TAXATION_TYPE_DOCTYPE_NAME,
    UNIT_OF_QUANTITY_DOCTYPE_NAME,
    UOM_CATEGORY_DOCTYPE_NAME,
    WORKSTATION_DOCTYPE_NAME,
)
from ..utils import get_link_value, get_company_from_setup_mapping


def send_pos_invoices_information() -> None:
    from ..overrides.server.sales_invoice import on_submit

    all_pending_pos_invoices: list[Document] = frappe.get_all(
        "POS Invoice", {"docstatus": 1, "custom_successfully_submitted": 0}, ["name"]
    )

    if all_pending_pos_invoices:
        for pos_invoice in all_pending_pos_invoices:
            doc = frappe.get_doc(
                "POS Invoice", pos_invoice.name, for_update=False
            )  # Refetch to get the document representation of the record

            try:
                on_submit(
                    doc, method=None
                )  # Delegate to the on_submit method for sales invoices

            except TypeError:
                continue


def update_documents(
    data: dict | list,
    doctype_name: str,
    field_mapping: dict,
    settings_name: str = None,
    is_table: bool = False,
    filter_field: str = "code",
    table_name: str = None,
    separator: str = " - ",
    fixed_values: dict = None  
) -> None:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON string: {data}")

    doc_list = data if isinstance(data, list) else data.get("results", [data])

    for record in doc_list:
        if isinstance(record, str):
            continue

        temp_doc = frappe.new_doc(doctype_name)
        
        if fixed_values:
            for field, value in fixed_values.items():
                setattr(temp_doc, field, value)

        for field, value in field_mapping.items():
            if isinstance(value, str):  
                setattr(temp_doc, field, record.get(value, ""))

        for field, value in field_mapping.items():
            if isinstance(value, dict) and "doctype" in value:
                linked_doctype = value.get("doctype")
                link_field = value.get("link_field")
                link_filter_field = value.get("filter_field", "custom_slade_id")
                link_extract_field = value.get("extract_field", "name")
                link_filter_value = record.get(link_field)
                if linked_doctype and link_filter_value:
                    linked_value = frappe.db.get_value(
                        linked_doctype,
                        {link_filter_field: link_filter_value},
                        link_extract_field,
                    )
                    setattr(temp_doc, field, linked_value or "")

        for field, value in field_mapping.items():
            if callable(value):  
                setattr(temp_doc, field, value(record))
            elif isinstance(value, dict) and "fields" in value:  
                parts = []
                for source_field in value["fields"]:
                    part = getattr(temp_doc, source_field, None)
                    if part is None:
                        part = record.get(source_field, "")
                    if part:
                        parts.append(str(part))
                setattr(temp_doc, field, separator.join(parts))

        filter_value = getattr(temp_doc, filter_field, None)
        if not filter_value:
            continue  

        filters = {filter_field: filter_value}
        if settings_name:
            if frappe.db.exists("DocField", {"parent": doctype_name, "fieldname": "settings"}):
                filters["settings"] = settings_name
            elif frappe.db.exists("DocField", {"parent": doctype_name, "fieldname": "custom_settings"}):
                filters["custom_settings"] = settings_name

        doc_name = frappe.db.get_value(doctype_name, filters, "name")
        
        if doc_name:
            doc = frappe.get_doc(doctype_name, doc_name)
            for field in field_mapping.keys():
                setattr(doc, field, getattr(temp_doc, field, ""))
            if fixed_values:
                for field, value in fixed_values.items():
                    setattr(doc, field, value)
        else:
            doc = temp_doc  

        if is_table and table_name and hasattr(doc, table_name):
            found = False
            for child_row in getattr(doc, table_name):
                if child_row.etims_setup == settings_name:
                    child_row.slade360_id = record.get("id")
                    child_row.is_active = 1
                    found = True
                    break
            
            if not found and settings_name:
                new_row = doc.append(table_name)
                new_row.etims_setup = settings_name
                new_row.slade360_id = record.get("id")
                new_row.is_active = 1 
                
        if settings_name and not is_table:
            if hasattr(doc, "settings"):
                doc.settings = settings_name
            elif hasattr(doc, "custom_settings"):
                doc.custom_settings = settings_name

        try:
            doc.save(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(f"Error updating {doctype_name}: {str(e)}")
            continue

    frappe.db.commit()
    
    
def update_unit_of_quantity(response: dict, settings_name: str, **kwargs) -> None:
    field_mapping = {
        "slade_id": "id",
        "code": "code",
        "sort_order": "sort_order",
        "code_name": "name",
        "code_description": "description",
    }
    update_documents(response, UNIT_OF_QUANTITY_DOCTYPE_NAME, field_mapping, settings_name=settings_name, is_table=True, table_name="etims_setup_mapping")


def update_packaging_units(response: dict, settings_name: str, **kwargs) -> None:
    field_mapping = {
        "slade_id": "id",
        "code": "code",
        "code_name": "name",
        "sort_order": "sort_order",
        "code_description": "description",
    }
    update_documents(response, PACKAGING_UNIT_DOCTYPE_NAME, field_mapping, settings_name=settings_name, is_table=True, table_name="etims_setup_mapping")



def update_payment_methods(response: dict, **kwargs) -> None:
    field_mapping = {
        "slade_id": "id",
        "account_details": "account_details",
        "mobile_money_type": "mobile_money_type",
        "mobile_money_business_number": "mobile_money_business_number",
        "bank_name": "bank_name",
        "bank_branch": "bank_branch",
        "bank_account_number": "bank_account_number",
        "active": lambda x: 1 if x.get("active") else 0,
        "code_name": "name",
        "description": "description",
        "account": "account",
    }
    update_documents(
        response, PAYMENT_TYPE_DOCTYPE_NAME, field_mapping, filter_field="slade_id"
    )


def update_currencies(response: dict, settings_name: str, **kwargs) -> None:
    field_mapping = {
        "custom_slade_id": "id",
        "currency_name": "iso_code",
        "enabled": lambda x: 1 if x.get("active") else 0,
        "custom_conversion_rate": "conversion_rate",
    }
    update_documents(response, "Currency", field_mapping, filter_field="currency_name", settings_name=settings_name, is_table=True, table_name="etims_setup_mapping")


def update_item_classification_codes(response: dict | list, **kwargs) -> None:
    field_mapping = {
        "slade_id": "id",
        "itemclscd": "classification_code",
        "itemclslvl": "classification_level",
        "itemclsnm": "classification_name",
        "taxtycd": "tax_type_code",
        "useyn": lambda x: 1 if x.get("is_used") else 0,
        "mjrtgyn": lambda x: 1 if x.get("is_frequently_used") else 0,
    }
    update_documents(
        response,
        ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
        field_mapping,
        filter_field="itemclscd",
    )


def update_taxation_type(response: dict, settings_name: str, **kwargs) -> None:
    field_mapping = {
        "slade_id": "id",
        "cd": "tax_code",
        "srtord": "sort_order",
        "cdnm": "name",
        "cddesc": "description",
    }
    update_documents(response, TAXATION_TYPE_DOCTYPE_NAME, field_mapping, filter_field="cd", settings_name=settings_name, is_table=True, table_name="etims_setup_mapping")


def update_countries(response: list, **kwargs) -> None:
    doc: Document | None = None
    for code, details in response.items():
        country_name = details.get("name", "").strip().lower()
        existing_doc = frappe.get_value(
            COUNTRIES_DOCTYPE_NAME, {"name": ["like", country_name]}
        )

        if existing_doc:
            doc = frappe.get_doc(COUNTRIES_DOCTYPE_NAME, existing_doc)
        else:
            doc = frappe.new_doc(COUNTRIES_DOCTYPE_NAME)

        doc.code = code
        doc.code_name = details.get("name")
        doc.currency_code = details.get("currency_code")
        doc.sort_order = details.get("sort_order", 0)
        doc.code_description = details.get("description", "")

        doc.save(ignore_permissions=True)

    frappe.db.commit()


def update_organisations(response: dict, **kwargs) -> None:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON string: {response}")

    record = (
        response if isinstance(response, list) else response.get("results", response)
    )[0]

    company_name = frappe.defaults.get_user_default("Company") or frappe.get_value(
        "Company", {}, "name"
    )

    doc = frappe.get_doc("Company", company_name)

    if record.get("default_currency"):
        doc.default_currency = (
            get_link_value(
                "Currency", "custom_slade_id", record.get("default_currency")
            )
            or "KES"
        )
    if record.get("web_address"):
        doc.website = record.get("web_address", "")
    if record.get("phone_number"):
        doc.phone_no = record.get("phone_number", "")
    if record.get("description"):
        doc.company_description = record.get("description", "")
    if record.get("id"):
        doc.custom_slade_id = record.get("id", "")
    if record.get("email_address"):
        doc.email = record.get("email_address", "")
    if record.get("tax_payer_pin"):
        doc.tax_id = record.get("tax_payer_pin", "")
    doc.is_etims_verified = 1 if record.get("is_etims_verified") else 0

    doc.save(ignore_permissions=True)

    frappe.db.commit()
    
    
def update_branches(response: dict, settings_name: str, **kwargs) -> None:
    if isinstance(response, str):
        try:
            response = frappe.parse_json(response)
        except ValueError:
            frappe.throw("Invalid JSON string in response")

    results = response.get("results", [response])

    for branch_data in results:
        if not isinstance(branch_data, dict):
            continue

        cluster_id = branch_data.get("parent")
        company = get_company_from_setup_mapping(cluster_id, settings_name)
        
        if not company:
            frappe.log_error(f"No company found for cluster {cluster_id}", "Branch Update Skipped")
            continue

        original_branch_name = branch_data.get('name', '').strip()
        if not original_branch_name:
            continue
            
        branch_name = f"eTims - {original_branch_name}"

        branch_filters = {
            "branch": branch_name,
        }
        branch_exists = frappe.db.exists("Branch", branch_filters)
        
        if branch_exists:
            branch = frappe.get_doc("Branch", branch_filters)
        else:
            branch = frappe.new_doc("Branch")

        branch.update({
            "company": company,
            "branch": branch_name,
            "slade_id": branch_data.get("id"),
            "tax_id": branch_data.get("organisation_tax_pin"),
            "etims_device_serial_no": branch_data.get("etims_device_serial_no"),
            "branch_code": branch_data.get("etims_branch_id"),
            "pin": branch_data.get("organisation_tax_pin"),
            "is_head_office": 1 if branch_data.get("is_headquater") else 0,
            "is_etims_branch": 1 if branch_data.get("branch_status") else 0,
            "is_etims_verified": 1 if branch_data.get("is_etims_verified") else 0,
        })

        branch.save(ignore_permissions=True)
        frappe.db.commit()


def update_departments(response: dict, **kwargs) -> None:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON string: {response}")

    record = (
        response if isinstance(response, list) else response.get("results", [response])
    )[0]

    department_name = "eTims Department"
    existing_department = frappe.db.get_value(
        "Department", {"department_name": department_name}, "name"
    )
    if existing_department:
        doc = frappe.get_doc("Department", existing_department)
    else:
        matching_department = frappe.db.get_value(
            "Department", {"department_name": department_name}, "name"
        )
        if matching_department:
            branch_name = record.get("parent_name", "")
            department_name = (
                f"{department_name} - {branch_name}" if branch_name else department_name
            )

        doc = frappe.new_doc("Department")
        doc.department_name = department_name

    if record.get("organisation"):
        doc.company = (
            get_link_value("Company", "custom_slade_id", record.get("organisation"))
            or frappe.defaults.get_user_default("Company")
            or frappe.get_value("Company", {}, "name")
        )
    if record.get("parent"):
        doc.custom_branch = get_link_value("Branch", "slade_id", record.get("parent"))
    if record.get("id"):
        doc.custom_slade_id = record.get("id")
    doc.is_etims_verified = 1 if record.get("is_etims_verified") else 0
    doc.custom_is_etims_department = 1

    doc.save(ignore_permissions=True)

    frappe.db.commit()


def update_workstations(response: dict, settings_name: str, **kwargs) -> None:
    field_mapping = {
        "slade_id": "id",
        "active": lambda x: 1 if x.get("active") else 0,
        "workstation": "name",
        "workstation_type_display": "workstation_type_display",
        "workstation_type": "workstation_type",
        "is_billing_point": lambda x: 1 if x.get("is_billing_point") else 0,
        "department": {
            "doctype": "Department",
            "link_field": "org_unit",
            "filter_field": "custom_slade_id",
            "extract_field": "name",
        },
    }
    update_documents(
        response, WORKSTATION_DOCTYPE_NAME, field_mapping, filter_field="slade_id", settings_name=settings_name
    )


def uom_category_search_on_success(response: dict, **kwargs) -> None:
    field_mapping = {
        "slade_id": "id",
        "measure_type": "measure_type",
        "category_name": "name",
        "active": lambda x: 1 if x.get("active") else 0,
    }
    update_documents(
        response, UOM_CATEGORY_DOCTYPE_NAME, field_mapping, filter_field="category_name"
    )


def uom_search_on_success(response: dict, **kwargs) -> None:
    field_mapping = {
        "custom_slade_id": "id",
        "custom_uom_type": "uom_type",
        "custom_factor": "factor",
        "custom_category": {
            "doctype": UOM_CATEGORY_DOCTYPE_NAME,
            "link_field": "category",
            "filter_field": "slade_id",
            "extract_field": "name",
        },
        "uom_name": "name",
        "active": lambda x: 1 if x.get("active") else 0,
    }
    update_documents(response, "UOM", field_mapping, filter_field="uom_name")


def warehouse_search_on_success(response: dict, settings_name: str, **kwargs) -> None:
    from ..apis.process_request import process_request
    from ..utils import get_settings

    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON string: {response}")

    doc_list = (
        response if isinstance(response, list) else response.get("results", [response])
    )

    settings = get_settings(settings_name=settings_name)

    bhfid_slade_id = frappe.db.get_value("Branch", settings.bhfid, "slade_id")
    selected_record = (
        next((r for r in doc_list if r.get("branch") == bhfid_slade_id), None)
        or next((r for r in doc_list if "Stock" in r.get("name", "")), None)
        or (doc_list[0] if doc_list else None)
    )
    if selected_record:
        existing_warehouse = frappe.db.get_value(
            "Warehouse", {"company": settings.company, "is_group": 1}, "name"
        )
        if existing_warehouse:
            frappe.db.set_value(
                "Warehouse",
                existing_warehouse,
                {
                    "custom_slade_id": selected_record.get("id", ""),
                },
            )
            frappe.db.set_value(
                SETTINGS_DOCTYPE_NAME,
                settings.name,
                {
                    "warehouse": existing_warehouse,
                },
            )
            frappe.enqueue(
                search_customer_supplier_locations, document_name=settings.name
            )

        bhfid_slade_id = frappe.db.get_value("Branch", settings.bhfid, "slade_id")
        if bhfid_slade_id:
            request_data = {
                "branch": bhfid_slade_id,
                "id": selected_record.get("id"),
            }
            frappe.enqueue(
                process_request,
                queue="default",
                is_async=True,
                doctype="Branch",
                request_data=request_data,
                route_key="LocationSearchReq",
                request_method="PATCH",
                settings_name=settings_name,
            )


def search_customer_supplier_locations(document_name: str) -> None:
    from ..apis.process_request import process_request

    process_request(
        {"location_type": "customer", "document_name": document_name},
        "LocationsSearchReq",
        search_customer_supplier_locations_on_success,
        doctype=SETTINGS_DOCTYPE_NAME,
    )

    process_request(
        {"location_type": "supplier", "document_name": document_name},
        "LocationsSearchReq",
        search_customer_supplier_locations_on_success,
        doctype=SETTINGS_DOCTYPE_NAME,
    )


def search_customer_supplier_locations_on_success(
    response: dict, document_name: str, **kwargs
) -> None:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON string: {response}")

    doc_list = (
        response if isinstance(response, list) else response.get("results", [response])
    )
    settings = frappe.get_doc(SETTINGS_DOCTYPE_NAME, document_name)
    bhfid_slade_id = frappe.db.get_value("Branch", settings.bhfid, "slade_id")
    selected_record = next(
        (r for r in doc_list if r.get("branch") == bhfid_slade_id), None
    ) or (doc_list[0] if doc_list else None)

    if selected_record:
        location_type = selected_record.get("location_type", "").lower()
        if location_type == "supplier":
            frappe.db.set_value(
                "Warehouse",
                settings.warehouse,
                "custom_slade_supplier_warehouse",
                selected_record.get("id"),
            )
        elif location_type == "customer":
            frappe.db.set_value(
                "Warehouse",
                settings.warehouse,
                "custom_slade_customer_warehouse",
                selected_record.get("id"),
            )


def pricelist_search_on_success(response: dict, **kwargs) -> None:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON string: {response}")

    doc_list = (
        response if isinstance(response, list) else response.get("results", [response])
    )

    for record in doc_list:
        if isinstance(record, str):
            continue

        existing_pricelist = frappe.db.get_value(
            "Price List", {"price_list_name": record.get("name")}, "name"
        )
        if existing_pricelist:
            doc = frappe.get_doc("Price List", existing_pricelist)
        else:
            doc = frappe.new_doc("Price List")

        doc.custom_slade_id = record.get("id")
        doc.custom_pricelist_status = record.get("pricelist_status")

        doc.custom_company = get_link_value(
            "Company", "custom_slade_id", record.get("organisation")
        )

        doc.custom_warehouse = get_link_value(
            "Warehouse", "custom_slade_id", record.get("location")
        )

        doc.price_list_name = record.get("name")

        if record.get("effective_from"):
            doc.custom_effective_from = frappe.utils.getdate(
                record.get("effective_from")
            )

        if record.get("effective_to"):
            doc.custom_effective_to = frappe.utils.getdate(record.get("effective_to"))

        doc.enabled = 1 if record.get("active") else 0
        doc.buying = 1 if record.get("pricelist_type") == "sales" else 0
        doc.selling = 1 if record.get("pricelist_type") == "purchases" else 0

        doc.save(ignore_permissions=True)

    frappe.db.commit()


def itemprice_search_on_success(response: dict, **kwargs) -> None:
    field_mapping = {
        "custom_slade_id": "id",
        "price_list_rate": "price_inclusive_tax",
        "custom_factor": "factor",
        "item_code": {
            "doctype": "Item",
            "link_field": "product",
            "filter_field": "custom_slade_id",
            "extract_field": "name",
        },
        "custom_company": {
            "doctype": "Company",
            "link_field": "organisation",
            "filter_field": "custom_slade_id",
            "extract_field": "name",
        },
        "currency": {
            "doctype": "Currency",
            "link_field": "currency",
            "filter_field": "custom_slade_id",
            "extract_field": "name",
        },
        "price_list": {
            "doctype": "Price List",
            "link_field": "pricelist",
            "filter_field": "custom_slade_id",
            "extract_field": "name",
        },
        "enabled": lambda x: 1 if x.get("active") else 0,
    }
    update_documents(
        response, "Item Price", field_mapping, filter_field="custom_slade_id"
    )


def operation_types_search_on_success(
    response: dict, document_name: str, **kwargs
) -> None:
    frappe.db.set_value(
        OPERATION_TYPE_DOCTYPE_NAME,
        document_name,
        {
            "slade_id": response.get("id"),
            "operation_name": response.get("operation_name"),
            "source_location": response.get("source_location"),
            "destination_location": response.get("destination_location"),
            "operation_type": response.get("operation_type"),
        },
    )

def update_clusters(response: dict, settings_name: str, **kwargs) -> None:
    pass
    # if isinstance(response, str):
    #     try:
    #         response = json.loads(response)
    #     except json.JSONDecodeError:
    #         raise ValueError(f"Invalid JSON string: {response}")

    # doc_list = response if isinstance(response, list) else response.get("results", [response])
    
    # modal_data = []
    # for record in doc_list:
    #     if isinstance(record, str):
    #         continue
            
    #     modal_data.append({
    #         "id": record.get("id"),
    #         "name": record.get("name"),
    #         "organisation": record.get("organisation")
    #     })
    
    # frappe.publish_realtime('show_cluster_matching_modal', {
    #     "data": modal_data,
    #     "settings_name": settings_name
    # })