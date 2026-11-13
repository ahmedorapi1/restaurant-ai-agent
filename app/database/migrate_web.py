"""Add the web demo schema. Run explicitly against the intended database."""

import os

import psycopg
from dotenv import load_dotenv
from app.database.menu_catalog import ensure_menu_columns

load_dotenv()


def migrate_web(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id SERIAL PRIMARY KEY,
                phone_number VARCHAR(20) NOT NULL UNIQUE,
                full_name VARCHAR(100) NOT NULL,
                address TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            ALTER TABLE customers
                ADD COLUMN IF NOT EXISTS demo_token_hash TEXT UNIQUE,
                ADD COLUMN IF NOT EXISTS food_preferences TEXT NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS disliked_foods TEXT NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS dietary_preferences TEXT NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS profile_summary TEXT NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id UUID PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        connection.execute("""
            ALTER TABLE orders
                ADD COLUMN IF NOT EXISTS customer_id INTEGER REFERENCES customers(id),
                ADD COLUMN IF NOT EXISTS confirmation_id UUID UNIQUE,
                ADD COLUMN IF NOT EXISTS delivery_phone VARCHAR(20),
                ADD COLUMN IF NOT EXISTS delivery_address TEXT
        """)
        ensure_menu_columns(connection)


if __name__ == "__main__":
    try:
        migrate_web(os.environ["DATABASE_URL"])
    except (KeyError, psycopg.Error) as error:
        raise SystemExit(f"Web migration failed ({type(error).__name__}). Check DATABASE_URL and the existing restaurant schema.") from None
    print("Web schema is ready.")
