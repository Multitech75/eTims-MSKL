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


def etims_log(level: str, *args, **kwargs):
    """
    Wrapper around etims_logger to allow multiple parameters.
    Usage:
        etims_log("error", "before_save_", doc)
        etims_log("debug", "payload", payload_dict)
    """
    # Convert all args to string and join with space for readability
    message = " ".join(str(a) for a in args)
    
    # Route to the right log level
    if level.lower() == "error":
        etims_logger.error(message, **kwargs)
    elif level.lower() == "warning":
        etims_logger.warning(message, **kwargs)
    else:
        etims_logger.debug(message, **kwargs)
