"""Run inside a dev stack's app container by `scripts/dev.sh onedrive-link`.

Finds or creates the /Expenses test folder at the top of the signed-in OneDrive and points every
car in this stack at it, so tests never write anywhere else. Prints no secrets.
"""
import os
import sys

from tmexpenses import db, onedrive

FOLDER = "Expenses test"

drive = onedrive.OneDrive(os.environ["EXPENSES_ENCRYPTION_KEY"])
try:
    with db.connect() as conn:
        account = db.onedrive_account(conn, lock=True)
        tokens = drive.refresh(account["client_id"], drive.decrypt(account["client_secret"]),
                               drive.decrypt(account["refresh_token"]))
        if tokens.get("refresh_token"):
            db.save_onedrive_account(conn, refresh_token=drive.encrypt(tokens["refresh_token"]))
    folder = drive.folder(tokens["access_token"], drive.ensure_subfolder(tokens["access_token"], None, FOLDER))
except onedrive.OneDriveError as exc:
    sys.exit(f"dev: OneDrive link failed: {exc}")
with db.connect() as conn:
    cars = db.cars(conn)
    for car in cars:
        db.set_car_folder(conn, car["id"], folder["id"], folder["path"])
print(f"dev: linked to OneDrive as {account['account']}; {len(cars)} car(s) use {folder['path']}")
