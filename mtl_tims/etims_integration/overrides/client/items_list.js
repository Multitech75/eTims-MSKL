const doctypeName = "Item";

frappe.listview_settings[doctypeName].onload = async function (listview) {
  try {
    const { message: settings } = await frappe.call({
      method: "mtl_tims.etims_integration.utils.get_etims_action_data",
      args: { doctype: doctypeName },
    });

    console.log("settings:", settings);
    if (!settings) return;
    
    // Add one button: Register all Items
    listview.page.add_inner_button(
      __("Register all Items"),
      function () {
        const args = { settings_name: settings.name };
        executeEtimsAction("submit_all", "Item", "Items registration queued");
      },
      __("eTims Actions")
    );
  } catch (err) {
    console.error("Error loading eTims settings:", err);
  }
};

// Helper: Run the Frappe call
function executeEtimsAction(method, doctype,successMsg) {
  console.log("Executing method:", method);
  frappe.call({
    method: `mtl_tims.etims_integration.apis.apis.${method}`,
    args:  { doctype },
    callback: () => frappe.msgprint(__(successMsg)),
    error: (err) => {
      // console.error(err);
      // frappe.msgprint(__("An error occurred during the request."));
    },
  });
}
