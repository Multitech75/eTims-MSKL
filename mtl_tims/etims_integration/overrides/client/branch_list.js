const doctypeName = "Branch";
const settingsDoctypeName = "Navari KRA eTims Settings";

frappe.listview_settings[doctypeName] = {
  onload: async function (listview) {
    const { message: activeSettings } = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_active_settings",
      args: {
        doctype: settingsDoctypeName,
      },
    });

    if (!activeSettings || activeSettings.length === 0) {
      console.log(
        "No active eTims settings found for Branches. 'Get Branches' button will not be displayed."
      );
      return;
    }

    const settingOptions = activeSettings.map((setting) => ({
      label: `${setting.company} (${setting.name})`,
      value: setting.name,
      company_name: setting.company,
    }));

    listview.page.add_inner_button(__("Get Branches"), async function () {
      let dialog = new frappe.ui.Dialog({
        title: __("Select Company Setup for Branch Fetch"),
        fields: [
          {
            label: __("Select Company Setup"),
            fieldname: "selected_settings_name",
            fieldtype: "Select",
            options: settingOptions,
            reqd: 1,
            default: settingOptions[0] ? settingOptions[0].value : null,
          },
        ],
        primary_action_label: __("Proceed"),
        primary_action: (data) => {
          const selectedSettingName = data.selected_settings_name;
          dialog.hide();

          const selectedSetting = activeSettings.find(
            (s) => s.name === selectedSettingName
          );
          const companyName = selectedSetting
            ? selectedSetting.company
            : frappe.boot.sysdefaults.company;

          frappe.call({
            method:
              "kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks.tasks.search_branch_request",
            args: {
              settings_name: selectedSettingName,
              request_data: {
                company_name: companyName,
              },
            },
            callback: (response) => {
              if (response.message) {
                frappe.msgprint(
                  __("Branch fetch request queued. Please check in later.")
                );
              } else if (response.exc) {
                frappe.msgprint({
                  title: __("Error"),
                  message: __(
                    "An error occurred on the server while queuing the request. Please check server logs."
                  ),
                  indicator: "red",
                });
                console.error("Server exception:", response.exc);
              }
              listview.refresh();
            },
            error: (error) => {
              frappe.msgprint({
                title: __("Network Error"),
                message: __(
                  "A network error occurred. Please check your internet connection and try again."
                ),
                indicator: "red",
              });
              console.error("Network error:", error);
            },
          });
        },
      });

      dialog.show();
    });
  },
};
