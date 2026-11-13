import hashlib
import os
import secrets
from contextlib import contextmanager
from uuid import UUID, uuid4

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

PROFILE_COLUMNS = """
    id, full_name, phone_number, address, food_preferences, disliked_foods,
    dietary_preferences, profile_summary, created_at, updated_at
"""


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5, row_factory=dict_row)


def check_web_schema() -> None:
    """Check the web migration before opening the LangGraph checkpointer."""
    with connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SELECT {PROFILE_COLUMNS}, demo_token_hash FROM customers LIMIT 0")
        conn.execute("SELECT id, customer_id FROM conversations LIMIT 0")
        conn.execute("SELECT id, customer_id, confirmation_id, delivery_phone, delivery_address FROM orders LIMIT 0")
        conn.execute("SELECT id, aliases, is_listed FROM menu_items LIMIT 0")
        conn.execute("SELECT order_id FROM order_items LIMIT 0")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_demo_customer() -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    # A reserved demo identifier fits the existing unique VARCHAR(20) phone column.
    demo_phone = "demo-" + secrets.token_hex(7)
    with connect() as conn:
        profile = conn.execute(f"""
            INSERT INTO customers (phone_number, full_name, demo_token_hash)
            VALUES (%s, %s, %s)
            RETURNING {PROFILE_COLUMNS}
        """, (demo_phone, "ضيف السفرة", token_digest(token))).fetchone()
    return token, profile


def get_demo_customer(token: str) -> dict | None:
    with connect() as conn:
        return conn.execute(f"""
            SELECT {PROFILE_COLUMNS}
            FROM customers WHERE demo_token_hash = %s
        """, (token_digest(token),)).fetchone()


def update_profile(customer_id: int, fields: dict) -> dict:
    with connect() as conn:
        return conn.execute(f"""
            UPDATE customers SET full_name = %s, address = %s,
                food_preferences = %s, disliked_foods = %s, dietary_preferences = %s,
                profile_summary = %s, updated_at = NOW()
            WHERE id = %s AND demo_token_hash IS NOT NULL
            RETURNING {PROFILE_COLUMNS}
        """, (
            fields["full_name"], fields["address"], fields["food_preferences"],
            fields["disliked_foods"], fields["dietary_preferences"],
            fields["profile_summary"], customer_id,
        )).fetchone()


def create_conversation(customer_id: int) -> UUID:
    thread_id = uuid4()
    with connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, customer_id) VALUES (%s, %s)",
            (thread_id, customer_id),
        )
    return thread_id


def owns_conversation(thread_id: UUID, customer_id: int) -> bool:
    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND customer_id = %s",
            (thread_id, customer_id),
        ).fetchone() is not None


def conversation_exists(thread_id: UUID) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM conversations WHERE id = %s", (thread_id,)).fetchone() is not None


@contextmanager
def conversation_lock(thread_id: UUID | str):
    # Separate connections, including in different API workers, share this lock.
    with connect() as conn:
        row = conn.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
            (str(thread_id),),
        ).fetchone()
        yield row["acquired"]
