"""Back up the live eTims settings before `bench migrate` and put them back afterwards.

Fixture sync and doctype sync run between `before_migrate` and `after_migrate`, and either can
overwrite site-specific records (URL, TIN, API key, environment). The backup is written to the
site's private folder, never to the app, so production credentials stay out of git.

Run a backup by hand at any time with:
    bench --site <site> execute mtl_tims.etims_integration.settings_backup.backup_settings
and restore the latest one with:
    bench --site <site> execute mtl_tims.etims_integration.settings_backup.restore_settings
"""

import json
import os

import frappe
from frappe.utils import now_datetime
from frappe.utils.password import get_decrypted_password, set_encrypted_password

BACKED_UP_DOCTYPES = ("eTims Company Settings", "eTims Tax Mapping")
SKIP_FIELDS = {"creation", "modified", "modified_by", "owner", "idx", "_user_tags", "_comments", "_assign", "_liked_by"}
KEEP_TIMESTAMPED = 10


def _backup_dir() -> str:
	path = frappe.get_site_path("private", "etims_settings_backup")
	os.makedirs(path, exist_ok=True)
	return path


def _latest_path() -> str:
	return os.path.join(_backup_dir(), "latest.json")


def _password_fields(doctype: str) -> list[str]:
	return [df.fieldname for df in frappe.get_meta(doctype).fields if df.fieldtype == "Password"]


def _snapshot() -> dict:
	data = {}
	for doctype in BACKED_UP_DOCTYPES:
		if not frappe.db.table_exists(doctype):
			continue
		password_fields = _password_fields(doctype)
		rows = []
		for row in frappe.get_all(doctype, fields=["*"], order_by="name asc"):
			row = {k: v for k, v in row.items() if k not in SKIP_FIELDS}
			for fieldname in password_fields:
				row[fieldname] = get_decrypted_password(doctype, row["name"], fieldname, raise_exception=False)
			rows.append(row)
		data[doctype] = rows
	return data


def backup_settings():
	"""Write the current eTims settings to the site's private folder. Safe to run any time."""
	data = _snapshot()
	if not any(data.values()):
		return

	payload = json.dumps({"taken_at": str(now_datetime()), "data": data}, indent=1, default=str)
	backup_dir = _backup_dir()

	stamped = os.path.join(backup_dir, f"etims_settings_{now_datetime().strftime('%Y%m%d_%H%M%S')}.json")
	for path in (stamped, _latest_path()):
		with open(path, "w") as f:
			f.write(payload)
		os.chmod(path, 0o600)

	# keep only the most recent timestamped copies
	stamped_files = sorted(f for f in os.listdir(backup_dir) if f.startswith("etims_settings_"))
	for old in stamped_files[:-KEEP_TIMESTAMPED]:
		os.remove(os.path.join(backup_dir, old))

	print(f"eTims settings backed up to {stamped}")


def restore_settings():
	"""Put back the settings from the latest backup wherever they differ from the database."""
	path = _latest_path()
	if not os.path.exists(path):
		return

	with open(path) as f:
		data = json.load(f).get("data", {})

	changed = []
	for doctype, rows in data.items():
		if not frappe.db.table_exists(doctype):
			continue
		password_fields = set(_password_fields(doctype))
		valid_columns = set(frappe.db.get_table_columns(doctype))

		for row in rows:
			name = row["name"]
			values = {
				k: v
				for k, v in row.items()
				if k in valid_columns and k not in password_fields and k != "name" and k != "doctype"
			}

			if frappe.db.exists(doctype, name):
				current = frappe.db.get_value(doctype, name, list(values), as_dict=True) or {}
				diff = {k: v for k, v in values.items() if str(current.get(k) or "") != str(v or "")}
				if diff:
					frappe.db.set_value(doctype, name, diff, update_modified=False)
					changed.append(f"{doctype} {name}: {', '.join(diff)}")
			else:
				doc = frappe.get_doc({"doctype": doctype, "name": name, **values})
				doc.db_insert()
				changed.append(f"{doctype} {name}: re-created")

			for fieldname in password_fields:
				saved = row.get(fieldname)
				if not saved:
					continue
				current = get_decrypted_password(doctype, name, fieldname, raise_exception=False)
				if current != saved:
					set_encrypted_password(doctype, name, saved, fieldname)
					frappe.db.set_value(doctype, name, fieldname, "*" * len(saved), update_modified=False)
					changed.append(f"{doctype} {name}: {fieldname}")

	frappe.db.commit()
	frappe.clear_cache()
	if changed:
		print("eTims settings restored from backup:\n  " + "\n  ".join(changed))
