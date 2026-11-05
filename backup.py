"""Copyright (c) 2026 ampohene. All rights reserved; see LICENSE.txt.

Safe daily backup: copies mis.db (even while the app is running) and zips the uploads folder
into ./backups, keeping the newest 14 of each. Run it by hand or as a scheduled task:
    python3 backup.py
Then also download a recent copy from ./backups to someone's own computer every week."""
import datetime, glob, os, shutil, sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("MIS_DB", os.path.join(BASE, "mis.db"))
UPLOADS = os.environ.get("MIS_UPLOADS", os.path.join(BASE, "uploads"))
OUT = os.path.join(BASE, "backups")
KEEP = 14

os.makedirs(OUT, exist_ok=True)
stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
if os.path.exists(DB):
    src, dst = sqlite3.connect(DB), sqlite3.connect(os.path.join(OUT, f"mis-{stamp}.db"))
    src.backup(dst)  # consistent copy, safe while people are using the app
    dst.close(); src.close()
if os.path.isdir(UPLOADS) and os.listdir(UPLOADS):
    shutil.make_archive(os.path.join(OUT, f"uploads-{stamp}"), "zip", UPLOADS)
for pattern in ("mis-*.db", "uploads-*.zip"):
    for old in sorted(glob.glob(os.path.join(OUT, pattern)))[:-KEEP]:
        os.remove(old)
print("Backup done:", stamp, "->", OUT)
