const customerDoctypeName = "Customer";

frappe.listview_settings[customerDoctypeName] = {
  onload: async function (listview) {
    try {
      // Prevent duplicate button creation if user reloads filters
      if (listview.page.custom_etims_buttons_added) return;
      listview.page.custom_etims_buttons_added = true;

      // Fetch eTims settings for this doctype
      const { message: settings } = await frappe.call({
        method: "mtl_tims.etims_integration.utils.get_etims_action_data",
        args: { doctype: customerDoctypeName },
      });

      if (!settings) return;

      const ACTION_GROUP = __("eTims Actions");

      // 🟢 Button 1: Submit all Customers
      listview.page.add_inner_button(
        __("Submit all Customers"),
        function () {
          executeEtimsCustomerAction(
            "submit_all",
            "Customer",
            "Customers submission queued"
          );
        },
        ACTION_GROUP
      );

      // 🟢 Action Menu: Bulk Submit Customers
      listview.page.add_action_item(__("Bulk Submit Customers"), function () {
        const customers = listview.get_checked_items().map((item) => item.name);
        if (!customers.length) {
          frappe.msgprint(__("Please select customers to submit"));
          return;
        }

        executeEtimsCustomerAction(
          "bulk_submit_customers",
          customers,
          "Bulk customer submission queued"
        );
      });
    } catch (err) {
      console.error("Error loading eTims settings:", err);

      // Optional: log to backend
      frappe.call({
        method: "mtl_tims.etims_integration.logger.etims_log",
        args: {
          level: "error",
          args: [`Error loading eTims settings: ${err.message || err}`],
        },
      });

      frappe.msgprint(__("Failed to load eTims settings."));
    }
  },
};

// 🔧 Helper: Run the Frappe call for eTims Customer Actions
function executeEtimsCustomerAction(method, data, successMsg) {
  frappe.call({
    method: `mtl_tims.etims_integration.apis.apis.${method}`,
    args: typeof data === "string" ? { doctype: data } : { docs_list: data },
    callback: () => frappe.msgprint(__(successMsg)),
    error: (err) => {
      console.error("eTims API Error:", err);
      frappe.call({
        method: "mtl_tims.etims_integration.logger.etims_log",
        args: {
          level: "error",
          args: [`Error calling ${method}: ${JSON.stringify(err)}`],
        },
      });
      frappe.msgprint(__("An error occurred during the request."));
    },
  });
}
