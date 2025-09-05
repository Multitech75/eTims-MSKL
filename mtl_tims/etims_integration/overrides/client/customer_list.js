const customerDoctypeName = "Customer";

frappe.listview_settings[customerDoctypeName] = {
  onload: async function (listview) {
    const { message: data } = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_etims_action_data",
      args: { doctype: customerDoctypeName },
    });

    const allSettings = data?.settings || [];
    if (!allSettings.length) return;

    listview.page.add_inner_button(
      __("Get Customers"),
      function () {
        showSettingsModalAndExecute(
          "Get Customers",
          allSettings,
          (settings_name) => ({
            method: "search_customers_request",
            args: { request_data: {}, settings_name: settings_name },
            success_msg: "Customer search queued",
          })
        );
      },
      __("eTims Actions")
    );

    listview.page.add_inner_button(
      __("Submit all Customers"),
      function () {
        showSettingsModalAndExecute(
          "Submit all Customers",
          allSettings,
          (settings_name) => ({
            method: "submit_all_customers",
            args: { settings_name: settings_name },
            success_msg: "Customer submission queued",
          })
        );
      },
      __("eTims Actions")
    );

    listview.page.add_action_item(__("Bulk Submit Customers"), function () {
      const customers = listview.get_checked_items().map((item) => item.name);
      if (!customers.length) {
        frappe.msgprint(__("Please select customers to submit"));
        return;
      }

      showSettingsModalAndExecute(
        "Bulk Submit Customers",
        allSettings,
        (settings_name) => ({
          method: "bulk_submit_customers",
          args: {
            docs_list: customers,
            settings_name: settings_name,
          },
          success_msg: "Bulk customer submission queued",
        })
      );
    });
  },
};

function showSettingsModalAndExecute(title, settings, getCallArgs) {
  if (settings.length === 1) {
    const { method, args, success_msg } = getCallArgs(settings[0].name);
    frappe.call({
      method: `kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.${method}`,
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
        method: `kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.${method}`,
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
