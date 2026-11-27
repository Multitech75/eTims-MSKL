"""Offline queue for eTims submissions.

When the eTims server (VSCU reached over ZeroTier) cannot be reached at all, Purchase
Invoices (incl. debit notes) and Stock Reconciliations are allowed to submit in ERPNext and
are marked as queued. A scheduled job resends them until eTims accepts or rejects them.

Sales Invoices are deliberately NOT queued: the printed invoice needs the KRA signature,
so they keep blocking submission when eTims is down.

A document is queued when its eTims message field starts with QUEUED_PREFIX. Using the
existing message field (instead of a new custom field) keeps old, never-sent documents
out of the resend job.
"""

import frappe
from frappe.model.document import Document

from .logger import etims_log

QUEUED_PREFIX = "QUEUED:"
QUEUED_MESSAGE = f"{QUEUED_PREFIX} eTIMS unreachable, will auto-resend"

# doctype -> field holding the eTims message (Data, 140 chars)
QUEUEABLE_DOCTYPES = {
    "Purchase Invoice": "custom_purchase_invoice_eTims_message",
    "Stock Reconciliation": "custom_stock_reconciliation_eTims_message",
}

RESEND_BATCH_SIZE = 50


class EtimsUnreachableError(frappe.ValidationError):
    """eTims could not be reached while resending a queued document."""


def queue_or_raise_if_unreachable(doc: Document, response: dict) -> bool:
    """Handle a failed eTims response caused by the server being unreachable.

    - During submit (docstatus 0): mark the document as queued and return True, so the
      caller lets the submission continue instead of throwing.
    - During a resend of an already submitted document: raise EtimsUnreachableError so the
      job leaves it queued.
    - Any other failure (eTims rejected it, or the connection broke mid-request): return
      False, and the caller throws as before.
    """
    if not response.get("connection_error") or doc.doctype not in QUEUEABLE_DOCTYPES:
        return False

    if doc.docstatus == 1:
        raise EtimsUnreachableError(response.get("message") or "eTims unreachable")

    message_field = QUEUEABLE_DOCTYPES[doc.doctype]
    doc.custom_submitted_successfully = 0
    doc.set(message_field, QUEUED_MESSAGE)
    doc.custom_eTims_response = frappe.as_json(response)

    etims_log("Warning", f"eTims unreachable, queued {doc.doctype} {doc.name}: {response.get('message')}")
    frappe.msgprint(
        msg=(
            f"eTIMS could not be reached, so {doc.doctype} <b>{doc.name}</b> was submitted and "
            "<b>queued</b>. It will be sent to eTIMS automatically when the connection is back."
        ),
        title="eTIMS Offline - Queued",
        indicator="orange",
    )
    return True


def resend_queued_documents() -> None:
    """Scheduled job: resend queued Purchase Invoices and Stock Reconciliations."""
    from .utils import get_settings

    settings_doc = get_settings()
    if not settings_doc or settings_doc.get("is_active") != 1:
        return

    for doctype, message_field in QUEUEABLE_DOCTYPES.items():
        names = frappe.get_all(
            doctype,
            filters={
                "docstatus": 1,
                "custom_submitted_successfully": 0,
                message_field: ["like", f"{QUEUED_PREFIX}%"],
            },
            order_by="creation asc",
            pluck="name",
            limit=RESEND_BATCH_SIZE,
        )
        for name in names:
            # Stop the run on the first unreachable error: the rest would fail the same way.
            if not _resend_one(doctype, name, message_field, settings_doc):
                return


def _resend_one(doctype: str, name: str, message_field: str, settings_doc) -> bool:
    """Resend one queued document. Returns False if eTims is still unreachable."""
    doc = frappe.get_doc(doctype, name)
    try:
        if doctype == "Purchase Invoice":
            from .overrides.server.purchase_invoice import submit_purchase_invoice

            submit_purchase_invoice(doc, settings_doc)
        else:
            from .overrides.server.stock_reconciliation import submit_stock_reconciliation

            submit_stock_reconciliation(doc, settings_doc)

        # The submit functions update fields in memory; persist them on the submitted doc.
        doc.db_update()
        for item in doc.get("items") or []:
            item.db_update()
        frappe.db.commit()
        etims_log("Debug", f"Queued {doctype} {name} sent to eTims: {doc.get(message_field)}")
        return True

    except EtimsUnreachableError as e:
        frappe.db.rollback()
        etims_log("Warning", f"eTims still unreachable, {doctype} {name} stays queued: {e}")
        return False

    except Exception as e:
        # eTims rejected it (or another error): stop retrying and surface it for manual action.
        frappe.db.rollback()
        error = str(e)
        frappe.db.set_value(
            doctype,
            name,
            {
                message_field: f"Resend failed: {error}"[:140],
                "custom_eTims_response": error,
            },
            update_modified=False,
        )
        frappe.db.commit()
        etims_log("Error", f"Queued {doctype} {name} rejected on resend: {error}")
        frappe.log_error(title=f"eTims resend failed: {doctype} {name}", message=frappe.get_traceback())
        return True
