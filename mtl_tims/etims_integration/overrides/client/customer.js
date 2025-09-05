const doctype = "Customer";

frappe.ui.form.on(doctype, {
  refresh: async function (frm) {
    let currency = frm.doc.default_currency || "KES";

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

    if (!allSettings.length || frm.is_new()) return;

    frappe.call({
      method: "frappe.client.get_value",
      args: {
        doctype: "Currency",
        fieldname: "name",
        filters: { name: currency },
      },
      callback: function (r) {
        if (r.message) {
          currency = r.message.name;
        }
      },
    });

    addCustomerActionButtons(frm, {
      allSettings,
      registeredMappings,
      unregisteredSettings,
    });
  },

  require_tax_id: function (frm) {
    frm.set_df_property("tax_id", "reqd", frm.doc.require_tax_id ? 1 : 0);
  },
});

function addCustomerActionButtons(frm, data) {
  const { allSettings, registeredMappings, unregisteredSettings } = data;

  if (frm.doc.tax_id && registeredMappings.length > 0) {
    frm.add_custom_button(
      __("Perform Customer Search"),
      () =>
        showCompanySelectionModal(
          frm,
          "search_customer",
          registeredMappings.map((r) => ({
            name: r.etims_setup,
            company: getCompanyName(allSettings, r.etims_setup),
          }))
        ),
      __("eTims Actions")
    );
  }

  if (unregisteredSettings.length > 0) {
    frm.add_custom_button(
      __("Send Customer Details"),
      () =>
        showCompanySelectionModal(frm, "send_customer", unregisteredSettings),
      __("eTims Actions")
    );
  }

  if (registeredMappings.length > 0) {
    frm.add_custom_button(
      __("Update Customer Details"),
      () =>
        showCompanySelectionModal(
          frm,
          "update_customer",
          registeredMappings.map((r) => ({
            name: r.etims_setup,
            company: getCompanyName(allSettings, r.etims_setup),
          }))
        ),
      __("eTims Actions")
    );
  }

  // if (registeredMappings.length > 0) {
  //   frm.add_custom_button(
  //     __("Get Customer Details"),
  //     () =>
  //       showCompanySelectionModal(
  //         frm,
  //         "get_customer_details",
  //         registeredMappings.map((r) => ({
  //           name: r.etims_setup,
  //           company: getCompanyName(allSettings, r.etims_setup),
  //         }))
  //       ),
  //     __("eTims Actions")
  //   );
  // }
}

function getCompanyName(allSettings, settingName) {
  const match = allSettings.find((s) => s.name === settingName);
  return match ? match.company : "Unknown";
}

function showCompanySelectionModal(frm, actionType, availableSettings) {
  if (!availableSettings.length) {
    frappe.msgprint(__("No available eTims settings for this action."));
    return;
  }

  if (availableSettings.length === 1) {
    executeCustomerAction(frm, actionType, availableSettings[0].name);
    return;
  }

  const options = availableSettings.map((setting) => ({
    label: `${setting.company} (${setting.name})`,
    value: setting.name,
  }));

  const dialog = new frappe.ui.Dialog({
    title: __("Select Company Setup"),
    fields: [
      {
        label: __("Select Company Setup"),
        fieldname: "selected_settings_name",
        fieldtype: "Select",
        options: options,
        reqd: 1,
        default: options[0]?.value || null,
      },
    ],
    primary_action_label: __("Proceed"),
    primary_action: (data) => {
      dialog.hide();
      executeCustomerAction(frm, actionType, data.selected_settings_name);
    },
  });

  dialog.show();
}

function executeCustomerAction(frm, actionType, settingsName) {
  let method, args, successMessage;
  let sladeId = "";
  if (frm.doc.etims_setup_mapping) {
    const mappingRow = frm.doc.etims_setup_mapping.find(
      (row) => row.etims_setup === settingsName
    );
    sladeId = mappingRow ? mappingRow.slade360_id : "";
  }

  switch (actionType) {
    case "search_customer":
      method =
        "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.perform_customer_search";
      args = {
        settings_name: settingsName,
        request_data: {
          doc_name: frm.doc.name,
          customer_pin: frm.doc.tax_id,
        },
      };
      successMessage = "Search queued. Please check in later.";
      break;

    case "send_customer":
    case "update_customer":
      method =
        "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.send_branch_customer_details";
      args = {
        name: frm.doc.name,
        settings_name: settingsName,
      };
      successMessage =
        actionType === "send_customer"
          ? "Customer details queued for registration."
          : "Customer details queued for update.";
      break;

    case "get_customer_details":
      method =
        "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.get_customer_details";
      args = {
        settings_name: settingsName,
        request_data: {
          doc_name: frm.doc.name,
          id: sladeId,
        },
      };
      successMessage = "Customer details fetch queued.";
      break;

    default:
      frappe.msgprint(__("Unknown action type."));
      return;
  }

  frappe.call({
    method: method,
    args: args,
    callback: () => frappe.msgprint(__(successMessage)),
    error: (err) => {
      console.error(err);
      frappe.msgprint(__("An error occurred during the request."));
    },
  });
}
