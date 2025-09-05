const itemDoctypName = "Item";

frappe.ui.form.on(itemDoctypName, {
  refresh: async function (frm) {
    const { message: data } = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_etims_action_data",
      args: {
        doctype: frm.doctype,
        docname: frm.doc.name,
      },
    });

    const allSettings = data?.settings || [];
    const registeredMappings = data?.registered_mappings || [];
    const unregisteredSettings = data?.unregistered_settings || [];

    if (!allSettings.length) return;

    if (frm.doc.custom_imported_item_submitted) {
      frm.toggle_enable("custom_referenced_imported_item", false);
      frm.toggle_enable("custom_imported_item_status", false);
    }

    if (!frm.is_new()) {
      const canRegister =
        frm.doc.custom_item_classification &&
        frm.doc.custom_taxation_type &&
        unregisteredSettings.length;

      if (canRegister) {
        frm.add_custom_button(
          __("Register Item"),
          function () {
            showCompanySelectionModal(
              frm,
              "register_item",
              unregisteredSettings
            );
          }, 
          __("eTims Actions")
        );
      }

      if (registeredMappings.length) {
        frm.add_custom_button(
          __("Fetch Item Details"),
          function () {
            showCompanySelectionModal(
              frm,
              "fetch_item_details",
              registeredMappings.map((r) => ({
                name: r.etims_setup,
                company: getCompanyName(allSettings, r.etims_setup),
              }))
            );
          },
          __("eTims Actions")
        );

        frm.add_custom_button(
          __("Update Item"),
          function () {
            showCompanySelectionModal(
              frm,
              "update_item",
              registeredMappings.map((r) => ({
                name: r.etims_setup,
                company: getCompanyName(allSettings, r.etims_setup),
              }))
            );
          },
          __("eTims Actions")
        );
      }

      if (frm.doc.is_stock_item && registeredMappings.length) {
        frm.add_custom_button(
          __("Submit Item Inventory"),
          function () {
            showCompanySelectionModal(
              frm,
              "submit_inventory",
              registeredMappings.map((r) => ({
                name: r.etims_setup,
                company: getCompanyName(allSettings, r.etims_setup),
              }))
            );
          },
          __("eTims Actions")
        );
      }
    }
  },

  custom_product_type_name: function (frm) {
    frm.set_value(
      "is_stock_item",
      frm.doc.custom_product_type_name !== "Service" ? 1 : 0
    );
  },
});

function getCompanyName(allSettings, settingName) {
  const match = allSettings.find((s) => s.name === settingName);
  return match ? match.company : "Unknown";
}

async function showCompanySelectionModal(frm, actionType, availableSettings) {
  if (!availableSettings.length) {
    frappe.msgprint(
      __(
        "No available eTims settings for this action. Please check configuration."
      )
    );
    return;
  }

  if (availableSettings.length === 1) {
    executeItemAction(frm, actionType, availableSettings[0].name);
    return;
  }

  const options = availableSettings.map((setting) => ({
    label: `${setting.company} (${setting.name})`,
    value: setting.name,
    company_name: setting.company,
  }));

  const fields = [
    {
      label: __("Select Company Setup"),
      fieldname: "selected_settings_name",
      fieldtype: "Select",
      options: options,
      reqd: 1,
      default: options[0]?.value || null,
    },
  ];

  const dialog = new frappe.ui.Dialog({
    title: __("Select Company Setup"),
    fields: fields,
    primary_action_label: __("Proceed"),
    primary_action: (data) => {
      const selectedSettingName = data.selected_settings_name;
      dialog.hide();
      executeItemAction(frm, actionType, selectedSettingName);
    },
  });

  dialog.show();
}

function executeItemAction(frm, actionType, settingName) {
  let method;
  let args = {};

  let sladeId = "";
  if (frm.doc.etims_setup_mapping) {
    const mappingRow = frm.doc.etims_setup_mapping.find(
      (row) => row.etims_setup === settingName
    );
    sladeId = mappingRow ? mappingRow.slade360_id : "";
  }

  switch (actionType) {
    case "register_item":
    case "update_item":
      method =
        "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.perform_item_registration";
      args = {
        item_name: frm.doc.name,
        settings_name: settingName,
      };
      break;

    case "fetch_item_details":
      method =
        "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.fetch_item_details";
      args = {
        settings_name: settingName,
        request_data: {
          document_name: frm.doc.name,
          id: sladeId,
        },
      };
      break;

    case "submit_inventory":
      method =
        "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.submit_inventory";
      args = {
        name: frm.doc.name,
        settings_name: settingName,
      };
      break;

    default:
      frappe.msgprint(__("Unknown action type."));
      return;
  }

  frappe.call({
    method: method,
    args: args,
    callback: () => {
      const messages = {
        register_item: "Item Registration Queued. Please check in later.",
        fetch_item_details: "Item Fetch Request Queued. Please check in later.",
        update_item: "Item Update Queued. Please check in later.",
        submit_inventory: "Inventory submission queued.",
      };
      frappe.msgprint(messages[actionType] || "Request queued.");
    },
    error: (error) => {
      frappe.msgprint(__("An error occurred during the request."));
      console.error(error);
    },
  });
}
