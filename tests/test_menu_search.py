import copy
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

# Searching fixtures must never use the application's database connection.
os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")

from app.database import menu_repository


MENU_ITEMS = [
    {
        "id": 13, "name": "كلاسيك برجر", "price": Decimal("110.00"),
        "is_available": False,
        "aliases": ["classic burger", "برجر كلاسيك", "كلاسيك برغر"],
    },
    {
        "id": 1, "name": "تشيز برجر", "price": Decimal("125.00"),
        "is_available": True,
        "aliases": ["cheese burger", "cheeseburger", "برجر جبنة", "برجر بالجبنة"],
    },
    {
        "id": 15, "name": "دبل برجر", "price": Decimal("155.00"),
        "is_available": None,
        "aliases": ["double burger", "برجر دبل", "برجر مزدوج"],
    },
    {
        "id": 16, "name": "بيتزا مارغريتا", "price": Decimal("140.00"),
        "is_available": None,
        "aliases": ["margherita pizza", "بيتزا مارجريتا", "مارجريتا"],
    },
    {
        "id": 17, "name": "بيتزا بيبروني", "price": Decimal("165.00"),
        "is_available": False,
        "aliases": ["pepperoni pizza", "بيتزا ببروني"],
    },
    {
        "id": 18, "name": "بيتزا خضار", "price": Decimal("150.00"),
        "is_available": True,
        "aliases": ["vegetable pizza", "veggie pizza", "بيتزا بالخضار"],
    },
    {
        "id": 3, "name": "بيبسي", "price": Decimal("25.00"),
        "is_available": True, "aliases": ["pepsi", "بيبسى", "ببسي"],
    },
    {
        "id": 29, "name": "تشيز كيك", "price": Decimal("70.00"),
        "is_available": None, "aliases": ["cheesecake", "cheese cake"],
    },
]


class MenuSearchTests(unittest.TestCase):
    def setUp(self):
        # Names, prices and IDs come from the existing menu. Availability includes
        # synthetic False/None values to exercise all three states without a DB.
        self.menu = copy.deepcopy(MENU_ITEMS)
        self.by_id = {item["id"]: item for item in self.menu}
        connection_patch = patch.object(menu_repository.psycopg, "connect")
        connect = connection_patch.start()
        self.addCleanup(connection_patch.stop)
        self.addCleanup(connect.assert_not_called)

    def search(self, query):
        return menu_repository.search_menu_items(self.menu, query)

    def assert_catalog_item(self, result):
        source = self.by_id[result["id"]]
        self.assertEqual(result["menu_item_id"], source["id"])
        self.assertEqual(result["name"], source["name"])
        self.assertEqual(result["price"], str(source["price"]))
        self.assertIs(result["is_available"], source["is_available"])
        self.assertIsInstance(result["score"], (int, float))

    def test_exact_name_and_normalized_arabic_return_original_item(self):
        for query in ("تشيز برجر", "  تَشِيز   بُرْجَر  "):
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(result["match_type"], "exact")
                self.assertEqual(result["item"]["id"], 1)
                self.assert_catalog_item(result["item"])

    def test_aliases_normalize_arabic_letters_and_english_case(self):
        for query, expected_id, expected_match in (
            ("برجر بالجبنة", 1, "alias"),
            ("برجر بالجبنه", 1, "alias"),
            ("  PEPSI  ", 3, "alias"),
            ("بيبسى", 3, "exact"),
        ):
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(result["match_type"], expected_match)
                self.assertEqual(result["item"]["id"], expected_id)
                self.assert_catalog_item(result["item"])

    def test_categories_return_all_variants_in_both_languages(self):
        for query, expected_ids in (
            ("برجر", {13, 1, 15}),
            ("burger", {13, 1, 15}),
            ("البرغر", {13, 1, 15}),
            ("بيتزا", {16, 17, 18}),
            ("pizza", {16, 17, 18}),
            ("عايز برجر", {13, 1, 15}),
        ):
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual(result["status"], "needs_confirmation")
                self.assertEqual(result["match_type"], "category")
                self.assertFalse(result.get("item"))
                self.assertEqual({item["id"] for item in result["suggestions"]}, expected_ids)
                for item in result["suggestions"]:
                    self.assert_catalog_item(item)

    def test_typo_requires_confirmation_even_for_one_plausible_candidate(self):
        result = self.search("تشيز برحر")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(result["match_type"], "fuzzy")
        self.assertFalse(result.get("item"))
        self.assertEqual({item["id"] for item in result["suggestions"]}, {1})
        for item in result["suggestions"]:
            self.assert_catalog_item(item)

    def test_quantity_and_partial_query_keep_all_category_choices(self):
        result = self.search("عايز ٢ برجر لو سمحت")
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(result["query"], "برجر")
        self.assertEqual({item["id"] for item in result["suggestions"]}, {1, 13, 15})
        partial = self.search("برج")
        self.assertEqual(partial["match_type"], "partial")
        self.assertEqual(partial["status"], "needs_confirmation")
        self.assertEqual({item["id"] for item in partial["suggestions"]}, {1, 13, 15})

    def test_unknown_or_too_short_queries_do_not_get_unrelated_suggestions(self):
        for query in ("سوشي", "zz", "x", "", "   "):
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual(result["status"], "not_found")
                self.assertEqual(result["match_type"], "none")
                self.assertFalse(result.get("item"))
                self.assertEqual(result["suggestions"], [])

    def test_missing_chicken_burger_is_not_silently_replaced(self):
        result = self.search("برجر دجاج")
        self.assertNotEqual(result["status"], "matched")
        self.assertFalse(result.get("item"))

    def test_hidden_historical_item_does_not_match_name_or_alias(self):
        self.menu.append({
            "id": 2, "name": "chicken purger", "price": Decimal("150.00"),
            "is_available": False, "aliases": ["برجر دجاج"], "is_listed": False,
        })
        for query in ("chicken purger", "برجر دجاج"):
            with self.subTest(query=query):
                result = self.search(query)
                self.assertNotEqual((result.get("item") or {}).get("id"), 2)
                self.assertNotIn(2, {item["id"] for item in result["suggestions"]})

    def test_order_matching_stays_exact_when_search_can_suggest_variants(self):
        self.assertEqual(menu_repository.match_menu_items(self.menu, "برجر"), [])
        self.assertEqual(menu_repository.match_menu_items(self.menu, "تشيز برحر"), [])
        self.assertEqual(menu_repository.match_menu_items(self.menu, "برجر دجاج"), [])
        matches = menu_repository.match_menu_items(self.menu, "برجر بالجبنة")
        self.assertEqual([item["id"] for item in matches], [1])


if __name__ == "__main__":
    unittest.main()
