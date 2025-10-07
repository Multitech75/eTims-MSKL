# """eTims Logger initialisation"""

# import frappe
# from frappe.utils import logger

# logger.set_log_level("DEBUG")
# etims_logger = frappe.logger("etims", allow_site=True, file_count=50)

"""eTims Logger initialisation"""

import frappe
from frappe.utils import logger

logger.set_log_level("DEBUG")
etims_logger = frappe.logger("etims", allow_site=True, file_count=50)


# @frappe.whitelist()
# def etims_log(level: str, *args, **kwargs):
#     """
#     Wrapper around etims_logger to allow multiple parameters.
#     Usage:
#         etims_log("error", "before_save_", doc)
#         etims_log("debug", "payload", payload_dict)
#     """
#     # Convert all args to string and join with space for readability
#     message = " ".join(str(a) for a in args)
    
#     # Route to the right log level
#     if level.lower() == "error":
#         etims_logger.error(message, **kwargs)
#     elif level.lower() == "warning":
#         etims_logger.warning(message, **kwargs)
#     else:
#         etims_logger.debug(message, **kwargs)

@frappe.whitelist()
def etims_log(level: str, *args, **kwargs):
    """
    Flexible logger that works for both JS frappe.call and direct Python calls.
    Usage examples:
        etims_log("error", "before_save_", doc)
        etims_log("error", ["msg1", "msg2"])  # if coming from frappe.call
    """
    # Handle case where Frappe sends args as a list (from JS)
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        args = args[0]

    # Convert all args to a readable string
    message = " ".join(str(a) for a in args) if args else ""

    # Route to appropriate log level
    level = level.lower()
    if level == "error":
        etims_logger.error(message, **kwargs)
    elif level == "warning":
        etims_logger.warning(message, **kwargs)
    else:
        etims_logger.debug(message, **kwargs)
