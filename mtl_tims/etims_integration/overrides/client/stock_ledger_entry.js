// Copyright (c) 2025, Navari Ltd and contributors
// For license information, please see license.txt

const doctypeName = "Stock Ledger Entry";
const settingsDoctypeName = "Navari KRA eTims Settings";

frappe.ui.form.on(doctypeName, {
  refresh: async function (frm) {
    const { message: activeSetting } = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_active_settings",
      args: {
        doctype: settingsDoctypeName,
      },
    });

    if (activeSetting?.length > 0) {
      if (!frm.is_new()) {
        if (!frm.doc.custom_slade_id) {
          frm.add_custom_button(
            __("Submit Stock Ledger Entry"),
            function () {
              frappe.call({
                method:
                  "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.stock_ledger_entry.save_ledger_details",
                args: {
                  name: frm.doc.name,
                },
                callback: (response) => {
                  frappe.msgprint(
                    "Submit request queued. Please check in later."
                  );
                },
                error: (r) => {
                  // Error Handling is Defered to the Server
                },
              });
            },
            __("eTims Actions")
          );
        } else {
          if (!frm.doc.custom_submitted_successfully) {
            if (frm.doc.custom_inventory_submitted_successfully) {
              frm.add_custom_button(
                __("Process Stock Ledger Entry"),
                function () {
                  frappe.call({
                    method:
                      "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.stock_ledger_entry.submit_stock_mvt_transition",
                    args: {
                      name: frm.doc.name,
                    },
                    callback: (response) => {
                      frappe.msgprint(
                        "Processing request queued. Please check in later."
                      );
                    },
                    error: (r) => {
                      // Error Handling is Defered to the Server
                    },
                  });
                },
                __("eTims Actions")
              );
            } else if (!frm.doc.custom_inventory_submitted_successfully) {
              frm.add_custom_button(
                __("Submit Stock Ledger Entry Line"),
                function () {
                  frappe.call({
                    method:
                      "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.stock_ledger_entry.submit_stock_mvt_items",
                    args: {
                      name: frm.doc.name,
                    },
                    callback: (response) => {
                      frappe.msgprint(
                        "Submit line request queued. Please check in later."
                      );
                    },
                    error: (r) => {
                      // Error Handling is Defered to the Server
                    },
                  });
                },
                __("eTims Actions")
              );
            }
          }
          frm.add_custom_button(
            __("Check Stock Ledger Entry Status"),
            function () {
              frappe.call({
                method:
                  "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.stock_ledger_entry.fetch_stock_mvt",
                args: {
                  name: frm.doc.name,
                },
                callback: (response) => {
                  frappe.msgprint(
                    "Status check request queued. Please check in later."
                  );
                },
                error: (r) => {
                  // Error Handling is Defered to the Server
                },
              });
            },
            __("eTims Actions")
          );
        }
      }
    }
  },
});
