
import os
import psycopg
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv
from uuid import UUID

from app.database.menu_repository import read_menu
from app.delivery import validate_delivery_details

load_dotenv()
DB_URI = os.environ["DATABASE_URL"]


def validate_order_id(order_id: str) -> str:
    """Validate before opening a connection and normalize the UUID for PostgreSQL."""
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("Order id is required")
    try:
        return str(UUID(order_id.strip()))
    except ValueError:
        raise ValueError("Order id must be a valid UUID") from None


def _confirmed_order(conn, confirmation_id: str, customer_id: int | None) -> dict | None:
    row = conn.execute("""
        SELECT id, total_amount, status
        FROM orders WHERE confirmation_id = %s AND customer_id IS NOT DISTINCT FROM %s
    """, (confirmation_id, customer_id)).fetchone()
    if row is None:
        return None
    return {"order_id": str(row[0]), "total_amount": str(row[1]), "status": row[2]}


def get_confirmed_order(confirmation_id: str, customer_id: int | None) -> dict | None:
    with psycopg.connect(DB_URI) as conn:
        return _confirmed_order(conn, confirmation_id, customer_id)


def create_order(customer_name: str, items: list[dict], *, delivery_phone: str | None = None,
                 delivery_address: str | None = None, customer_id: int | None = None,
                 confirmation_id: str | None = None):
    delivery = validate_delivery_details({
        "full_name": customer_name, "phone_number": delivery_phone, "address": delivery_address,
    })
    if not isinstance(confirmation_id, str):
        raise ValueError("A valid order confirmation is required")
    try:
        confirmation_id = str(UUID(confirmation_id))
    except ValueError:
        raise ValueError("A valid order confirmation is required") from None
    if not isinstance(items, list) or not items:
        raise ValueError("Order must contain at least one item")

    normalized_items = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each order item must contain an ID, quantity, and quoted price")
        item_id, quantity = item.get("menu_item_id"), item.get("quantity")
        if type(item_id) is not int or not 0 < item_id <= 2_147_483_647:
            raise ValueError("Menu item id must be a positive integer")
        if type(quantity) is not int or not 0 < quantity <= 2_147_483_647:
            raise ValueError("Quantity must be a positive PostgreSQL integer")
        unit_price = item.get("unit_price")
        if not isinstance(unit_price, (str, Decimal)):
            raise ValueError("A valid quoted price is required")
        try:
            quoted_price = Decimal(unit_price)
        except InvalidOperation:
            raise ValueError("A valid quoted price is required") from None
        if not quoted_price.is_finite() or quoted_price <= 0:
            raise ValueError("A valid quoted price is required")
        normalized_items.append((item_id, quantity, quoted_price))

    # One transaction covers the menu validation, order and all its lines.
    with psycopg.connect(DB_URI) as conn:
        existing = _confirmed_order(conn, confirmation_id, customer_id)
        if existing:
            return existing

        ids = [item_id for item_id, _, _ in normalized_items]
        locked = {row["id"]: row for row in read_menu(conn, ids, lock=True)}

        validated_items = []
        total_amount = Decimal("0.00")
        for item_id, quantity, quoted_price in normalized_items:
            row = locked.get(item_id)
            if row is None:
                raise ValueError(f"Item not found: #{item_id}")
            if row["is_available"] is not True:
                raise ValueError(f"Item unavailable: {row['name']}")
            price = row["price"]
            if price != quoted_price:
                raise ValueError(f"Item price changed: {row['name']}")
            total_amount += price * quantity
            validated_items.append((item_id, quantity, price))
        if total_amount > Decimal("99999999.99"):
            raise ValueError("Order total exceeds the supported amount")

        # Snapshot these details on the order so later profile changes cannot change it.
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO orders (
                    customer_name, delivery_phone, delivery_address, total_amount, customer_id, confirmation_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (confirmation_id) DO NOTHING
                RETURNING id
            """, (
                delivery["full_name"], delivery["phone_number"], delivery["address"],
                total_amount, customer_id, confirmation_id,
            ))
            inserted = cursor.fetchone()
            if inserted is None:
                existing = _confirmed_order(conn, confirmation_id, customer_id)
                if existing:
                    return existing
                raise ValueError("Confirmation is already in use")
            order_id = inserted[0]

            for item_id, quantity, price in validated_items:
                cursor.execute("""
                    INSERT INTO order_items (order_id, menu_item_id, quantity, unit_price)
                    VALUES (%s, %s, %s, %s)
                """, (order_id, item_id, quantity, price))

    return {"order_id": str(order_id), "total_amount": str(total_amount), "status": "pending"}


def _order_filter(order_id: str, customer_id: int | None) -> tuple[str, tuple]:
    order_id = validate_order_id(order_id)
    if customer_id is None:
        return "id = %s", (order_id,)
    return "id = %s AND customer_id = %s", (order_id, customer_id)


def cancel_order(order_id: str, *, customer_id: int | None = None):
    condition, params = _order_filter(order_id, customer_id)

    with psycopg.connect(DB_URI) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                UPDATE orders SET status = 'cancelled'
                WHERE {condition} AND status = 'pending'
                RETURNING id;
            """, params)
            result = cursor.fetchone()

            if result is not None:
                return {"order_id": str(result[0]), "status": "cancelled"}

            cursor.execute(f"SELECT status FROM orders WHERE {condition};", params)
            if cursor.fetchone() is None:
                raise ValueError("Order not found")
            raise ValueError("Only pending orders can be cancelled")

def get_order_status(order_id: str, *, customer_id: int | None = None):
    condition, params = _order_filter(order_id, customer_id)

    with psycopg.connect(DB_URI) as conn:
        with conn.cursor() as cursor:

            cursor.execute(f"""
                SELECT customer_name, status, total_amount
                FROM orders
                WHERE {condition};
            """, params)

            result = cursor.fetchone()

            if result is None:
                raise ValueError("Order not found")

            return {
                "order_id": params[0],
                "customer_name": result[0],
                "status": result[1],
                "total_amount": str(result[2])
            }

