import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

# Set before importing app modules so .env cannot select a real database.
os.environ["DATABASE_URL"] = "postgresql://unused:unused@127.0.0.1:1/unused"

import psycopg

from app.agent import tools as tool_module
from app.database import order_repository as repository


class ToolTests(unittest.TestCase):
    def test_invalid_uuid_never_connects(self):
        with patch.object(repository.psycopg, "connect") as connect:
            for order_id in ("", " ", "bad-id", "'; DROP TABLE orders; --"):
                for restaurant_tool in (tool_module.cancel_1order, tool_module.check_order_status):
                    with self.subTest(tool=restaurant_tool.name, order_id=order_id):
                        result = restaurant_tool.invoke({"order_id": order_id})
                        self.assertFalse(result["success"])
                        self.assertIn("error", result)
            connect.assert_not_called()

    def test_menu_and_empty_menu_use_same_envelope(self):
        with patch.object(tool_module, "get_all_menu_items", return_value=[]) as get_menu:
            self.assertTrue(tool_module.get_all_items_of_the_menu.invoke({})["success"])
            get_menu.return_value = [{"name": "pepsi", "price": Decimal("30.00"), "is_available": True}]
            result = tool_module.get_all_items_of_the_menu.invoke({})
            self.assertTrue(result["success"])
            self.assertIn("30.00", result["menu"])
            self.assertIn("pepsi", result["menu"])

    def test_database_errors_are_safe_for_catalog_and_order_queries(self):
        cases = [
            (tool_module.get_all_items_of_the_menu, "get_all_menu_items", {}),
            (tool_module.resolve_menu_item, "get_all_menu_items", {"item_name": "pepsi"}),
            (tool_module.cancel_1order, "cancel_order", {"order_id": str(uuid4())}),
            (tool_module.check_order_status, "get_order_status", {"order_id": str(uuid4())}),
        ]
        for restaurant_tool, dependency, arguments in cases:
            with self.subTest(tool=restaurant_tool.name):
                with patch.object(tool_module, dependency, side_effect=psycopg.OperationalError("SECRET_CONNECTION_DETAIL")):
                    with self.assertLogs(tool_module.logger, level="WARNING") as logs:
                        result = restaurant_tool.invoke(arguments)
                    self.assertFalse(result["success"])
                    self.assertNotIn("SECRET_CONNECTION_DETAIL", json.dumps(result) + str(logs.output))

    def test_invalid_tool_arguments_do_not_raise(self):
        with patch.object(repository.psycopg, "connect") as connect:
            for quantity in (0, -1, True, 1.5, "2", 2_147_483_648):
                with self.subTest(quantity=quantity):
                    result = tool_module.create_new_order.invoke({"customer_name": "Test", "items": [{"item_name": "pepsi", "quantity": quantity}]})
                    self.assertFalse(json.loads(result)["success"])
            connect.assert_not_called()
        for restaurant_tool in (tool_module.create_new_order, tool_module.resolve_menu_item, tool_module.cancel_1order, tool_module.check_order_status):
            self.assertFalse(json.loads(restaurant_tool.invoke({}))["success"])

    def test_bad_repository_input_never_connects(self):
        delivery = {"delivery_phone": "01012345678", "delivery_address": "12 Nile Street, Dokki"}
        line = {"menu_item_id": 1, "quantity": 1, "unit_price": "25.00"}
        cases = [
            (" ", [line]), ("Sara Ali", []), ("Sara Ali", [{}]), ("Sara Ali", [None]),
            ("Sara Ali", [{**line, "menu_item_id": 0}]),
            ("Sara Ali", [{**line, "quantity": True}]),
            ("Sara Ali", [{**line, "quantity": 0}]),
            ("Sara Ali", [{**line, "unit_price": "NaN"}]),
            ("x" * 101, [line]),
        ]
        with patch.object(repository.psycopg, "connect") as connect:
            for customer, items in cases:
                with self.subTest(customer=customer, items=items):
                    with self.assertRaises(ValueError):
                        repository.create_order(customer, items, confirmation_id=str(uuid4()), **delivery)
            connect.assert_not_called()

    def test_direct_placement_requires_delivery_and_final_confirmation(self):
        with patch.object(repository.psycopg, "connect") as connect:
            result = tool_module.create_new_order.invoke({
                "customer_name": "Sara Ali", "items": [{"menu_item_id": 1, "quantity": 1}],
            })
            self.assertFalse(result["success"])
            self.assertNotIn(tool_module.create_new_order, tool_module.tools)
            line = {"menu_item_id": 1, "quantity": 1, "unit_price": "25.00"}
            with self.assertRaisesRegex(ValueError, "Delivery details"):
                repository.create_order("Sara Ali", [line], confirmation_id=str(uuid4()))
            with self.assertRaisesRegex(ValueError, "confirmation"):
                repository.create_order("Sara Ali", [line], delivery_phone="01012345678", delivery_address="12 Nile Street, Dokki")
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
