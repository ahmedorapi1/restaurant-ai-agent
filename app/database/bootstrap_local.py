"""Prepare the Sofra Compose database using the existing schema and seed."""

import os
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict


def bootstrap_local() -> None:
    settings = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    database_url = settings.get("DATABASE_URL")
    password = settings.get("SOFRA_DB_PASSWORD")
    port = settings.get("SOFRA_DB_PORT", "5432")
    expected = {
        "host": "127.0.0.1",
        "port": port,
        "dbname": "restaurant_demo",
        "user": "sofra",
        "password": password,
    }
    if (
        not database_url
        or not password
        or password == "replace_me"
        or port not in {"5432", "55432"}
        or conninfo_to_dict(database_url) != expected
    ):
        raise SystemExit("Set matching Sofra local database settings in .env. See README.md.")
    if os.environ.get("DATABASE_URL", database_url) != database_url:
        raise SystemExit("Remove the shell DATABASE_URL override before using local setup.")

    # These modules capture DATABASE_URL at import time.
    os.environ["DATABASE_URL"] = database_url
    from app.agent.checkpointer import create_checkpointer
    from app.database.init_db import init_db_and_seed_data
    from app.database.migrate_web import migrate_web

    init_db_and_seed_data()
    migrate_web(database_url)
    with create_checkpointer() as checkpointer:
        checkpointer.setup()
    print("Sofra schema, menu seed, and checkpoints are ready.")


if __name__ == "__main__":
    try:
        bootstrap_local()
    except psycopg.Error as error:
        raise SystemExit(f"Sofra database setup failed ({type(error).__name__}). Check local PostgreSQL and .env.") from None
