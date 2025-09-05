const parentDoctype = "Purchase Invoice";
const settingsDoctypeName = "Navari KRA eTims Settings";

frappe.ui.form.on(parentDoctype, {
  refresh: async function (frm) {
    const { message: activeSetting } = await frappe.call({
      method:
        "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.get_active_settings",
      args: {
        doctype: settingsDoctypeName,
      },
    });

    if (activeSetting?.length > 0 && frm.doc.docstatus !== 0) {
      if (!frm.doc.custom_submitted_successfully) {
        frm.add_custom_button(
          __("Send Invoice"),
          function () {
            frappe.call({
              method:
                "kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.purchase_invoice.send_purchase_details",
              args: {
                name: frm.doc.name,
              },
              callback: (response) => {},
              error: (r) => {
                // Error Handling is Defered to the Server
              },
            });
          },
          __("eTims Actions")
        );
      }
    }
  },
});
