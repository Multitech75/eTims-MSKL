import frappe


OLD_DOCTYPE = "eTims Unit Packing"
NEW_DOCTYPE = "eTims Quantity Packing"


def execute() -> None:
    old_exists = frappe.db.exists("DocType", OLD_DOCTYPE)
    new_exists = frappe.db.exists("DocType", NEW_DOCTYPE)

    if old_exists and new_exists:
        frappe.throw(
            f"Cannot rename {OLD_DOCTYPE}: {NEW_DOCTYPE} already exists."
        )

    if old_exists:
        frappe.rename_doc(
            "DocType",
            OLD_DOCTYPE,
            NEW_DOCTYPE,
            force=True,
            show_alert=False,
        )
    elif new_exists and frappe.db.table_exists(OLD_DOCTYPE):
        new_table_rows = frappe.db.sql(
            f"SELECT COUNT(*) FROM `tab{NEW_DOCTYPE}`"
        )[0][0]
        if new_table_rows:
            frappe.throw(
                f"Cannot finish renaming {OLD_DOCTYPE}: "
                f"the {NEW_DOCTYPE} table already contains records."
            )

        frappe.db.sql(f"DROP TABLE `tab{NEW_DOCTYPE}`")
        frappe.db.rename_table(OLD_DOCTYPE, NEW_DOCTYPE)
