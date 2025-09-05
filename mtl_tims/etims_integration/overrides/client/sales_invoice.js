const parentDoctype = "Sales Invoice";
const childDoctype = `${parentDoctype} Item`;
const packagingUnitDoctypeName = "Navari eTims Packaging Unit";
const unitOfQuantityDoctypeName = "Navari eTims Unit of Quantity";
const taxationTypeDoctypeName = "Navari KRA eTims Taxation Type";
const settingsDoctypeName = "Navari KRA eTims Settings";

frappe.realtime.on("refresh_form", function (name) {
  const currentForm = cur_frm;
  if (currentForm && currentForm.doc.name === name) {
    currentForm.reload_doc();
  }
});

frappe.ui.form.on(parentDoctype, {
  refresh: async function (frm) {
    await updateTaxAmountLabel(frm);
    const { message: activeSetting } = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_active_settings",
      args: { doctype: settingsDoctypeName },
    });

    if (
      activeSetting?.length > 0 &&
      frm.doc.docstatus !== 0 &&
      !frm.doc.prevent_etims_submission
    ) {
      if (!frm.doc.custom_successfully_submitted) {
        frm.add_custom_button(
          __("Send Invoice"),
          function () {
            showSettingsModalAndExecute(
              "Send Invoice",
              activeSetting,
              (settings_name) => ({
                method:
                  "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.sales_invoice.send_invoice_details",
                args: {
                  name: frm.doc.name,
                  settings_name: settings_name,
                },
                success_msg: "Invoice submission queued",
              })
            );
          },
          __("eTims Actions")
        );
      }

      if (frm.doc.custom_successfully_submitted && !frm.doc.custom_qr_code) {
        frm.add_custom_button(
          __("Sync Invoice Details"),
          function () {
            showSettingsModalAndExecute(
              "Sync Invoice",
              activeSetting,
              (settings_name) => ({
                method:
                  "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.get_invoice_details",
                args: {
                  document_name: frm.doc.name,
                  invoice_type: "Sales Invoice",
                  settings_name: settings_name,
                  company: frm.doc.company,
                },
                success_msg: "Invoice sync queued",
              })
            );
          },
          __("eTims Actions")
        );
      }

      frm.add_custom_button(
        __("Verify Submission and Resend if Incorrect"),
        function () {
          showSettingsModalAndExecute(
            "Verify Submission and Resend if Incorrect",
            activeSetting,
            (settings_name) => ({
              method:
                "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.get_invoice_details",
              args: {
                document_name: frm.doc.name,
                invoice_type: "Sales Invoice",
                settings_name: settings_name,
                company: frm.doc.company,
              },
              success_msg: "Verification and correction queued",
            })
          );
        },
        __("eTims Actions")
      );
    }
  },
});

function showSettingsModalAndExecute(title, settings, getCallArgs) {
  if (settings.length === 1) {
    const { method, args, success_msg } = getCallArgs(settings[0].name);
    frappe.call({
      method: method,
      args: args,
      callback: () => frappe.msgprint(__(success_msg)),
      error: (err) => {
        console.error(err);
        frappe.msgprint(__("An error occurred during the request."));
      },
    });
    return;
  }

  const dialog = new frappe.ui.Dialog({
    title: __(title),
    fields: [
      {
        label: __("Select eTims Settings"),
        fieldname: "settings_name",
        fieldtype: "Select",
        options: settings.map((s) => ({
          label: `${s.company} (${s.name})`,
          value: s.name,
        })),
        reqd: 1,
        default: settings[0]?.name,
      },
    ],
    primary_action_label: __("Proceed"),
    primary_action: ({ settings_name }) => {
      dialog.hide();
      const { method, args, success_msg } = getCallArgs(settings_name);
      frappe.call({
        method: method,
        args: args,
        callback: () => frappe.msgprint(__(success_msg)),
        error: (err) => {
          console.error(err);
          frappe.msgprint(__("An error occurred during the request."));
        },
      });
    },
  });
  dialog.show();
}

frappe.ui.form.on(childDoctype, {
  item_code: function (frm, cdt, cdn) {
    const item = locals[cdt][cdn].item_code;
    const taxationType = locals[cdt][cdn].custom_taxation_type;

    if (!taxationType) {
      frappe.db.get_value(
        "Item",
        { item_code: item },
        ["custom_taxation_type"],
        (response) => {
          locals[cdt][cdn].custom_taxation_type = response.custom_taxation_type;
          locals[cdt][cdn].custom_taxation_type_code =
            response.custom_taxation_type;
        }
      );
    }
  },
  custom_packaging_unit: async function (frm, cdt, cdn) {
    const packagingUnit = locals[cdt][cdn].custom_packaging_unit;

    if (packagingUnit) {
      frappe.db.get_value(
        packagingUnitDoctypeName,
        {
          name: packagingUnit,
        },
        ["code"],
        (response) => {
          const code = response.code;
          locals[cdt][cdn].custom_packaging_unit_code = code;
          frm.refresh_field("custom_packaging_unit_code");
        }
      );
    }
  },
  custom_unit_of_quantity: function (frm, cdt, cdn) {
    const unitOfQuantity = locals[cdt][cdn].custom_unit_of_quantity;

    if (unitOfQuantity) {
      frappe.db.get_value(
        unitOfQuantityDoctypeName,
        {
          name: unitOfQuantity,
        },
        ["code"],
        (response) => {
          const code = response.code;
          locals[cdt][cdn].custom_unit_of_quantity_code = code;
          frm.refresh_field("custom_unit_of_quantity_code");
        }
      );
    }
  },
});

async function updateTaxAmountLabel(frm) {
  try {
    const defaultCompany = frappe.defaults.get_user_default("Company");
    if (!defaultCompany) return;

    const { message: companyDoc } = await frappe.db.get_value(
      "Company",
      defaultCompany,
      "default_currency"
    );

    if (companyDoc?.default_currency) {
      const currency = companyDoc.default_currency;

      frm.fields_dict.items.grid.update_docfield_property(
        "custom_tax_amount",
        "label",
        `Tax Amount (${currency})`
      );
    }
  } catch (error) {
    console.error("Error updating Tax Amount label:", error);
  }
}
