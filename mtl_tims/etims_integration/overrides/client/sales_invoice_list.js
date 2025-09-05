const doctypeName = "Sales Invoice";
const settingsDoctypeName = "Navari KRA eTims Settings";

frappe.listview_settings[doctypeName].onload = async function (listview) {
  const { message: activeSetting } = await frappe.call({
    method:
      "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_active_settings",
    args: { doctype: settingsDoctypeName },
  });

  if (activeSetting?.length > 0) {
    listview.page.add_action_item(
      __("Bulk Submit to eTims"),
      function () {
        showSettingsModalAndExecute(
          "Bulk Submit to eTims",
          activeSetting,
          (settings_name) => ({
            method: "bulk_submit_sales_invoices",
            args: {
              docs_list: listview.get_checked_items().map((item) => item.name),
              settings_name: settings_name,
            },
            success_msg: "Bulk submission to eTims queued.",
          })
        );
      },
      __("eTims Actions")
    );

    listview.page.add_inner_button(
      __("Submit All Invoices"),
      function () {
        showSettingsModalAndExecute(
          "Submit All Invoices",
          activeSetting,
          (settings_name) => ({
            method: "bulk_submit_sales_invoices",
            args: { docs_list: null, settings_name: settings_name },
            success_msg: "Bulk submission to eTims queued.",
          })
        );
      },
      __("eTims Actions")
    );

    listview.page.add_action_item(
      __("Verify & Resend to eTims"),
      function () {
        showSettingsModalAndExecute(
          "Verify & Resend to eTims",
          activeSetting,
          (settings_name) => ({
            method: "bulk_verify_and_resend_invoices",
            args: {
              docs_list: listview.get_checked_items().map((item) => item.name),
              settings_name: settings_name,
            },
            success_msg:
              "Bulk verification queued. Incorrect invoices will be resent to eTims.",
          })
        );
      },
      __("eTims Actions")
    );

    listview.page.add_inner_button(
      __("Verify & Resend All Invoices"),
      function () {
        showSettingsModalAndExecute(
          "Verify & Resend All Invoices",
          activeSetting,
          (settings_name) => ({
            method: "bulk_verify_and_resend_invoices",
            args: { docs_list: null, settings_name: settings_name },
            success_msg:
              "Bulk verification queued. Incorrect invoices will be resent to eTims.",
          })
        );
      },
      __("eTims Actions")
    );
  }
};

function showSettingsModalAndExecute(title, settings, getCallArgs) {
  executeWithSingleOrDialog(
    settings,
    (settingsName) => {
      const { method, args, success_msg } = getCallArgs(settingsName);
      executeEtimsAction(method, args, success_msg);
    },
    () => {
      const options = settings.map((s) => ({
        label: `${s.company} (${s.name})`,
        value: s.name,
      }));

      const dialog = new frappe.ui.Dialog({
        title: __(title),
        fields: [
          {
            label: __("Select eTims Settings"),
            fieldname: "settings_name",
            fieldtype: "Select",
            options: options,
            reqd: 1,
            default: options[0]?.value,
          },
        ],
        primary_action_label: __("Proceed"),
        primary_action: ({ settings_name }) => {
          dialog.hide();
          const { method, args, success_msg } = getCallArgs(settings_name);
          executeEtimsAction(method, args, success_msg);
        },
      });
      dialog.show();
    }
  );
}

function executeWithSingleOrDialog(settings, actionFn, buildDialog) {
  if (settings.length === 1) {
    actionFn(settings[0].name);
    return;
  }
  buildDialog();
}

function executeEtimsAction(method, args, successMsg) {
  frappe.call({
    method: `kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.${method}`,
    args: args,
    callback: () => frappe.msgprint(__(successMsg)),
    error: (err) => {
      console.error(err);
      frappe.msgprint(__("An error occurred during the request."));
    },
  });
}
