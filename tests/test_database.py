import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict

# Never fall back to the real .env URL, including during module import.
os.environ["DATABASE_URL"] = "postgresql://unused:unused@127.0.0.1:1/unused"

from app.agent import tools as tool_module
from app.database import init_db, menu_repository, order_repository
from app.database.migrate_web import migrate_web

TEST_URL = os.getenv("RESTAURANT_TEST_DATABASE_URL")
DELIVERY_DETAILS = {"delivery_phone": "01012345678", "delivery_address": "١٢ شارع النيل، الدقي، الجيزة"}


@unittest.skipUnless(TEST_URL, "Set RESTAURANT_TEST_DATABASE_URL to an EMPTY disposable local test database")
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        info = conninfo_to_dict(TEST_URL)
        if info.get("host") not in ("127.0.0.1", "localhost") or info.get("dbname") != "restaurant_agent_test":
            raise RuntimeError("Integration tests require a dedicated local restaurant_agent_test database")
        # Refuse populated databases: fixtures must never overlap existing order data.
        with psycopg.connect(TEST_URL) as conn:
            tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
            if tables:
                raise RuntimeError("Integration tests require an EMPTY disposable database")
        cls.patches = [patch.object(module, "DB_URI", TEST_URL) for module in (init_db, menu_repository, order_repository)]
        for replacement in cls.patches:
            replacement.start()
            cls.addClassCleanup(replacement.stop)
        init_db.init_db_and_seed_data()
        migrate_web(TEST_URL)
        cls.menu = {item["name"]: item for item in menu_repository.get_all_menu_items()}

    def quoted_item(self, name, quantity):
        item = self.menu[name]
        return {"menu_item_id": item["id"], "quantity": quantity, "unit_price": str(item["price"])}

    def create_order(self, items=None, *, confirmation_id=None):
        return order_repository.create_order(
            " علي حسن ", [self.quoted_item("بيبسي", 2)] if items is None else items,
            confirmation_id=confirmation_id or str(uuid4()), **DELIVERY_DETAILS,
        )

    def test_creation_menu_and_status(self):
        self.assertTrue(tool_module.get_all_items_of_the_menu.invoke({})["success"])
        result = self.create_order()
        UUID(result["order_id"])
        self.assertEqual(result["total_amount"], "50.00")
        with psycopg.connect(TEST_URL) as conn:
            delivery = conn.execute("""
                SELECT customer_name, delivery_phone, delivery_address FROM orders WHERE id = %s
            """, (result["order_id"],)).fetchone()
        self.assertEqual(delivery, (
            "علي حسن", DELIVERY_DETAILS["delivery_phone"], DELIVERY_DETAILS["delivery_address"],
        ))
        status = tool_module.check_order_status.invoke({"order_id": result["order_id"].upper()})
        self.assertTrue(status["success"])
        self.assertEqual(status["order_id"], result["order_id"])
        self.assertEqual(status["status"], "pending")
        self.assertNotIn("delivery_phone", status)
        self.assertNotIn("delivery_address", status)

    def test_missing_unavailable_and_overflow_reject_without_orders(self):
        with psycopg.connect(TEST_URL) as conn:
            before = conn.execute("SELECT count(*) FROM orders").fetchone()[0]
        for item, message in (
            ({"menu_item_id": 2_147_483_647, "quantity": 1, "unit_price": "25.00"}, "Item not found"),
            (self.quoted_item("بطاطس مقلية", 1), "Item unavailable"),
            (self.quoted_item("بيبسي", 2_147_483_647), "Order total exceeds"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.create_order([item])
        with psycopg.connect(TEST_URL) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM orders").fetchone()[0], before)

    def test_local_order_confirmation_is_idempotent(self):
        confirmation_id = str(uuid4())
        first = self.create_order(confirmation_id=confirmation_id)
        self.assertEqual(self.create_order(confirmation_id=confirmation_id), first)

    def test_cancellation_all_existing_status_rules(self):
        for initial in ("pending", "preparing", "ready", "delivered", "cancelled"):
            with self.subTest(status=initial):
                order_id = self.create_order()["order_id"]
                with psycopg.connect(TEST_URL) as conn:
                    conn.execute("UPDATE orders SET status = %s WHERE id = %s", (initial, order_id))
                result = tool_module.cancel_1order.invoke({"order_id": order_id})
                self.assertEqual(result["success"], initial == "pending")
                self.assertEqual(order_repository.get_order_status(order_id)["status"], "cancelled" if initial == "pending" else initial)
                if initial != "pending":
                    self.assertEqual(result["error"], "Only pending orders can be cancelled")

    def test_unknown_uuid_returns_not_found(self):
        for restaurant_tool in (tool_module.cancel_1order, tool_module.check_order_status):
            result = restaurant_tool.invoke({"order_id": str(uuid4())})
            self.assertEqual(result, {"success": False, "error": "Order not found"})

    def test_insertion_failure_rolls_back_parent_and_items(self):
        # Fail the SECOND child insert after a parent and first child were written.
        class FailingCursor(psycopg.Cursor):
            def execute(self, query, params=None, **kwargs):
                if "INSERT INTO order_items" in query and params[2] == 2:
                    raise psycopg.IntegrityError("Synthetic failure in isolated test")
                return super().execute(query, params, **kwargs)

        real_connect = psycopg.connect

        def test_connect(*args, **kwargs):
            return real_connect(*args, cursor_factory=FailingCursor, **kwargs)

        with real_connect(TEST_URL) as conn:
            before = tuple(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("orders", "order_items"))
        with patch.object(order_repository.psycopg, "connect", side_effect=test_connect), self.assertRaises(psycopg.IntegrityError):
            self.create_order([self.quoted_item("بيبسي", 1), self.quoted_item("بيبسي", 2)])
        with real_connect(TEST_URL) as conn:
            after = tuple(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("orders", "order_items"))
        self.assertEqual(after, before)

    def test_concurrent_cancellation_succeeds_only_once(self):
        order_id = self.create_order()["order_id"]

        def cancel():
            return tool_module.cancel_1order.invoke({"order_id": order_id})

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: cancel(), range(2)))
        self.assertEqual(sum(result["success"] for result in results), 1)

    def test_conversation_survives_process_restart(self):
        root = Path(__file__).resolve().parents[1]
        thread_id = "restart-test-" + str(uuid4())
        environment = {**os.environ, "DATABASE_URL": TEST_URL}
        for mode in ("write", "read"):
            result = subprocess.run([sys.executable, "-m", "tests.persistence_worker", mode, thread_id], cwd=root, env=environment, capture_output=True, text=True, timeout=60)
            # Avoid echoing exception details that could include credentials.
            self.assertEqual(result.returncode, 0, f"Persistence worker failed during {mode}; inspect the disposable test environment")


if __name__ == "__main__":
    unittest.main()
