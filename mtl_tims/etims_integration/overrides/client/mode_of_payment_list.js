const doctypeName = "Mode of Payment";
const settingsDoctypeName = "Navari KRA eTims Settings";

frappe.listview_settings[doctypeName] = {
  onload: async function (listview) {
    const { message: activeSetting } = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_active_settings",
      args: {
        doctype: settingsDoctypeName,
      },
    });

    if (activeSetting?.length > 0) {
      listview.page.add_inner_button(
        __("Submit all to eTims"),
        function () {
          frappe.call({
            method:
              "kenya_compliance_via_slade.kenya_compliance_via_slade.apis.apis.send_all_mode_of_payments",

            callback: (response) => {},
            error: (r) => {
              // Error Handling is Defered to the Server
            },
          });
        },
        __("eTims Actions")
      );
    }
  },
};
