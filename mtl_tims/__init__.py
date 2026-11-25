__version__ = "0.0.1"


def check_app_permission():
	"""Show eTims on the apps screen for users who can read its settings."""
	import frappe

	if frappe.session.user == "Administrator":
		return True
	return bool(frappe.has_permission("eTims Company Settings", "read"))
