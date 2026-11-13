"""Apply the reviewed menu to local development after checking its backup."""

import hashlib
import json
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.database.menu_catalog import MENU_IMAGE_SHA256, ensure_menu_columns, reconcile_menu


def sync(backup_path: Path):
    root = Path(__file__).resolve().parents[2]
    url = dotenv_values(root / ".env")["DATABASE_URL"]
    config = conninfo_to_dict(url)
    if (
        config.get("host") != "127.0.0.1"
        or config.get("port") not in {"5432", "55432"}
        or config.get("dbname") != "restaurant_demo"
        or config.get("hostaddr", "127.0.0.1") != "127.0.0.1"
        or config.get("service")
    ):
        raise SystemExit("Menu sync is limited to the local Sofra demo database.")
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    if (
        backup.get("table") != "public.menu_items"
        or backup.get("database") != "restaurant_demo"
        or str(backup.get("port")) != config.get("port")
    ):
        raise SystemExit("Backup does not match the local Sofra menu.")
    digest = hashlib.sha256((root / "assets/menu.png").read_bytes()).hexdigest()
    if digest != MENU_IMAGE_SHA256 or digest != backup.get("menu_image_sha256"):
        raise SystemExit("Menu image changed. Review its transcription before syncing.")

    with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute("SET LOCAL search_path = public")
        conn.execute("LOCK TABLE menu_items IN SHARE ROW EXCLUSIVE MODE")
        rows = conn.execute("SELECT * FROM menu_items ORDER BY id").fetchall()
        if json.loads(json.dumps(rows, default=str)) != backup["rows"]:
            raise SystemExit("Menu changed since the backup. Take a fresh backup before syncing.")
        with conn.cursor() as cursor:
            ensure_menu_columns(cursor)
            reconcile_menu(cursor)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m app.database.sync_menu <menu-backup.json>")
    try:
        sync(Path(sys.argv[1]))
    except psycopg.Error as error:
        raise SystemExit(f"Menu sync failed ({type(error).__name__}).") from None
    print("Verified menu applied.")
