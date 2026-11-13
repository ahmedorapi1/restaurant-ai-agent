import json
import os
import re
import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")

from app.agent import checkpointer, runner
from app.agent.menu_image import get_menu_image
from app import api as api_module
from app.api import create_app
from app.database import customer_repository, init_db, menu_repository, order_repository
from app.database.migrate_web import migrate_web
from tests.web_model import OfflineRestaurantModel

WEB_TEST_URL = os.getenv("RESTAURANT_WEB_TEST_DATABASE_URL")
DELIVERY_NAME = "سلمى حسن"
DELIVERY_PHONE = "01012345678"
DELIVERY_ADDRESS = "١٢ شارع النيل، الدقي، الجيزة"
DELIVERY_MESSAGE = f"الاسم: {DELIVERY_NAME}\nرقم الموبايل: {DELIVERY_PHONE}\nالعنوان: {DELIVERY_ADDRESS}"


def offline_delivery_updates(text, _missing):
    return {
        DELIVERY_MESSAGE: {"full_name": DELIVERY_NAME, "phone_number": DELIVERY_PHONE, "address": DELIVERY_ADDRESS},
        "سلمى": {"full_name": "سلمى"},
        DELIVERY_NAME: {"full_name": DELIVERY_NAME},
        "12345": {"phone_number": "12345"},
        "+20 101 234 5678": {"phone_number": "+20 101 234 5678"},
        "الدقي": {"address": "الدقي"},
        DELIVERY_ADDRESS: {"address": DELIVERY_ADDRESS},
        "العنوان الجديد: ١٨ شارع التحرير، الدقي، الجيزة": {"address": "١٨ شارع التحرير، الدقي، الجيزة"},
    }.get(text, {})


class StartupTests(unittest.TestCase):
    def test_missing_schema_is_reported_before_starting_checkpointer(self):
        with patch.object(customer_repository, "check_web_schema", side_effect=psycopg.errors.UndefinedColumn):
            with patch.object(api_module, "create_restaurant_agent") as create_agent:
                with self.assertRaisesRegex(RuntimeError, "web schema is incomplete"):
                    with TestClient(create_app()):
                        self.fail("The application accepted an incomplete schema")
                create_agent.assert_not_called()

    def test_gpt6_omits_custom_temperature(self):
        for model_name, temperature in (("gpt-6-luna", None), ("gpt-4o-mini", 0)):
            with (
                self.subTest(model=model_name),
                patch.object(runner, "OPENAI_MODEL", model_name),
                patch.object(runner, "create_checkpointer"),
                patch.object(runner, "ChatOpenAI") as model,
                patch.object(runner, "create_agent"),
                runner.create_restaurant_agent(web=True),
            ):
                model.assert_called_once_with(model=model_name, temperature=temperature, timeout=30, max_retries=0)


class MenuImageTests(unittest.TestCase):
    def test_remote_config_returns_structured_reference(self):
        url = "https://menu.example.test/restaurant.webp"
        with patch.dict(os.environ, {"MENU_IMAGE_URL": url, "MENU_IMAGE_PATH": ""}):
            self.assertEqual(get_menu_image.invoke({}), {"success": True, "type": "image", "url": url})

    def test_local_and_missing_image_config(self):
        with patch.dict(os.environ, {"MENU_IMAGE_URL": "", "MENU_IMAGE_PATH": "assets/menu.png"}):
            self.assertEqual(get_menu_image.invoke({})["url"], "/api/menu/image")
        with patch.dict(os.environ, {"MENU_IMAGE_URL": "javascript:alert(1)", "MENU_IMAGE_PATH": ""}):
            self.assertFalse(get_menu_image.invoke({})["success"])


@unittest.skipUnless(WEB_TEST_URL, "Set RESTAURANT_WEB_TEST_DATABASE_URL to an empty local restaurant_web_test database")
class WebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        info = conninfo_to_dict(WEB_TEST_URL)
        if info.get("host") not in ("127.0.0.1", "localhost") or info.get("dbname") != "restaurant_web_test":
            raise RuntimeError("Web tests require a dedicated local restaurant_web_test database")
        with psycopg.connect(WEB_TEST_URL) as conn:
            if conn.execute("SELECT 1 FROM pg_tables WHERE schemaname = 'public' LIMIT 1").fetchone():
                raise RuntimeError("Web tests require an empty, disposable database")
            # Reproduce the existing customer table and a legacy row before migration.
            conn.execute("""
                CREATE TABLE customers (
                    id SERIAL PRIMARY KEY, phone_number VARCHAR(20) UNIQUE NOT NULL,
                    full_name VARCHAR(100) NOT NULL, address TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("INSERT INTO customers (phone_number, full_name) VALUES ('01000000000', 'Private Legacy Customer')")

        patches = [
            patch.dict(os.environ, {
                "DATABASE_URL": WEB_TEST_URL,
                "FRONTEND_ORIGINS": "http://127.0.0.1:5173",
                "MENU_IMAGE_PATH": "assets/menu.png",
                "MENU_IMAGE_URL": "",
            }),
            patch.object(init_db, "DB_URI", WEB_TEST_URL),
            patch.object(menu_repository, "DB_URI", WEB_TEST_URL),
            patch.object(order_repository, "DB_URI", WEB_TEST_URL),
            patch.object(checkpointer, "DB_URL", WEB_TEST_URL),
            patch.object(runner, "ChatOpenAI", return_value=OfflineRestaurantModel()),
            patch.object(api_module, "create_delivery_extractor", return_value=offline_delivery_updates),
        ]
        for replacement in patches:
            replacement.start()
            cls.addClassCleanup(replacement.stop)
        init_db.init_db_and_seed_data()
        with psycopg.connect(WEB_TEST_URL) as conn:
            cls.legacy_order = str(conn.execute("""
                INSERT INTO orders (customer_name, total_amount)
                VALUES ('Private Legacy Customer', 25.00) RETURNING id
            """).fetchone()[0])
        migrate_web(WEB_TEST_URL)
        migrate_web(WEB_TEST_URL)

    def identity(self, client):
        response = client.post("/api/customers/demo")
        self.assertEqual(response.status_code, 201, response.text)
        demo = response.json()
        return demo["profile"], {"Authorization": "Bearer " + demo["token"]}

    def conversation(self, client, headers):
        response = client.post("/api/conversations", headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def send(self, client, headers, thread, text, *, confirmation_id=None, status_code=200):
        body = {"message": text}
        if confirmation_id is not None:
            body["confirmation_id"] = confirmation_id
        response = client.post(f"/api/conversations/{thread}/messages", headers=headers, json=body)
        self.assertEqual(response.status_code, status_code, response.text)
        return response.json()

    def confirm(self, client, headers, chat, decision="approve", *, status_code=200):
        response = client.post(
            f"/api/conversations/{chat['thread_id']}/confirmation",
            headers=headers,
            json={"confirmation_id": chat["pending_confirmation"]["id"], "decision": decision},
        )
        self.assertEqual(response.status_code, status_code, response.text)
        return response.json()

    def start_final_checkout(self, client, headers, quote):
        self.assertEqual(quote["pending_confirmation"]["stage"], "proceed")
        collecting = self.confirm(client, headers, quote)
        self.assertIsNone(collecting["pending_confirmation"])
        final = self.send(client, headers, quote["thread_id"], DELIVERY_MESSAGE)
        self.assertEqual(final["pending_confirmation"]["stage"], "final")
        self.assertNotEqual(final["pending_confirmation"]["id"], quote["pending_confirmation"]["id"])
        summary = final["messages"][-1]["text"]
        for text in (DELIVERY_NAME, DELIVERY_PHONE, DELIVERY_ADDRESS, "رسوم التوصيل لسه مش محددة"):
            self.assertIn(text, summary)
        return final

    def order_rows(self):
        with psycopg.connect(WEB_TEST_URL) as conn:
            return conn.execute("SELECT id, customer_id, status FROM orders ORDER BY id").fetchall()

    def test_messages_persist_across_backend_lifespans(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            self.assertEqual(client.post(f"/api/conversations/{thread}/messages", headers=headers, json={"message": "  "}).status_code, 422)
            reply = self.send(client, headers, thread, "first memory")
            self.assertEqual([m["role"] for m in reply["messages"]], ["customer", "assistant"])
        with TestClient(create_app()) as client:
            restored = client.get(f"/api/conversations/{thread}/messages", headers=headers)
            self.assertEqual(restored.json()["messages"], reply["messages"])
            self.assertEqual(self.send(client, headers, thread, "after restart")["messages"][-1]["text"], "first memory | after restart")

    def test_health_distinguishes_database_failure_without_exposing_details(self):
        with TestClient(create_app()) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            with patch.object(customer_repository, "connect", side_effect=psycopg.OperationalError("private database detail")):
                result = client.get("/health")
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json(), {"detail": "Database temporarily unavailable"})

    def test_new_chat_preserves_explicit_profile_and_leaves_pending_action_behind(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            result = client.put("/api/customers/me", headers=headers, json={
                "full_name": "Demo Profile",
                "address": "Example address",
                "food_preferences": "cheese burgers",
                "disliked_foods": "spicy food",
                "dietary_preferences": "vegetarian options",
                "profile_summary": "explicitly saved",
            })
            self.assertEqual(result.status_code, 200, result.text)
            original = self.conversation(client, headers)["thread_id"]
            self.send(client, headers, original, "old unfinished conversation")
            before = self.order_rows()
            proposal = self.send(client, headers, original, "create")
            self.assertIsNotNone(proposal["pending_confirmation"])
            fresh = self.conversation(client, headers)
            self.assertNotEqual(fresh["thread_id"], original)
            self.assertEqual(fresh["messages"], [])
            self.assertIsNone(fresh["pending_confirmation"])
            self.assertEqual(self.order_rows(), before)
            old = client.get(f"/api/conversations/{original}/messages", headers=headers)
            self.assertEqual(old.json()["pending_confirmation"], proposal["pending_confirmation"])
            answer = self.send(client, headers, fresh["thread_id"], "profile")
            self.assertEqual([m["text"] for m in answer["messages"]], ["profile", "Demo Profile | cheese burgers"])
        with TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/customers/me", headers=headers).json()["disliked_foods"], "spicy food")

    def test_customers_cannot_read_or_write_each_others_conversations(self):
        with TestClient(create_app()) as client:
            first, first_headers = self.identity(client)
            second, second_headers = self.identity(client)
            self.assertNotEqual(first["id"], second["id"])
            self.assertNotIn("demo_token_hash", first)
            thread = self.conversation(client, first_headers)["thread_id"]
            route = f"/api/conversations/{thread}/messages"
            self.assertEqual(client.get(route).status_code, 401)
            self.assertEqual(client.get(route, headers=second_headers).status_code, 404)
            self.assertEqual(client.post(route, headers=second_headers, json={"message": "hello"}).status_code, 404)
            self.assertEqual(client.post(f"/api/conversations/{thread}/confirmation", headers=second_headers,
                json={"confirmation_id": "guessed", "decision": "approve"}).status_code, 404)
            phone_login = client.get("/api/customers/me", headers={"Authorization": "Bearer 01000000000"})
            self.assertEqual(phone_login.status_code, 401)
            self.assertEqual(client.get("/api/customers/me", headers=second_headers).json()["id"], second["id"])

    def test_real_agent_requires_confirmation_and_scopes_order_operations(self):
        with TestClient(create_app()) as client:
            owner, headers = self.identity(client)
            _, other_headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            proposal = self.send(client, headers, thread, "create")
            self.assertEqual(proposal["pending_confirmation"]["actions"], [{
                "kind": "create", "details": ["بيبسي × 1 — 25.00 جنيه للوحدة", "إجمالي الأصناف: 25.00 جنيه"],
            }])
            self.assertEqual(self.order_rows(), before)
            self.send(client, headers, thread, "approve in text")
            self.assertEqual(self.order_rows(), before)
            final = self.start_final_checkout(client, headers, proposal)
            self.assertEqual(self.confirm(client, headers, proposal), final)
            self.assertEqual(self.order_rows(), before)
            answer = self.confirm(client, headers, final)
            order_id = re.search(r"[0-9a-f-]{36}", answer["messages"][-1]["text"]).group()
            self.assertIn((order_id, owner["id"], "pending"), [(str(id_), cid, status) for id_, cid, status in self.order_rows()])
            self.assertIsNone(answer["pending_confirmation"])
            self.assertEqual(self.confirm(client, headers, final), answer)
            self.assertEqual(len(self.order_rows()), len(before) + 1)
            with psycopg.connect(WEB_TEST_URL) as conn:
                stored = conn.execute("""
                    SELECT customer_name, delivery_phone, delivery_address, confirmation_id::text
                    FROM orders WHERE id = %s
                """, (order_id,)).fetchone()
            self.assertEqual(stored, (DELIVERY_NAME, DELIVERY_PHONE, DELIVERY_ADDRESS, final["pending_confirmation"]["id"]))
            self.assertEqual(client.get("/api/customers/me", headers=headers).json()["full_name"], owner["full_name"])

            other_thread = self.conversation(client, other_headers)["thread_id"]
            for hidden_id in (order_id, self.legacy_order):
                status = self.send(client, other_headers, other_thread, "status:" + hidden_id)
                self.assertEqual(json.loads(status["messages"][-1]["text"])["error"], "Order not found")
                cancel = self.send(client, other_headers, other_thread, "cancel:" + hidden_id)
                denied = self.confirm(client, other_headers, cancel)
                self.assertEqual(json.loads(denied["messages"][-1]["text"])["error"], "Order not found")
            own = self.send(client, headers, thread, "status:" + order_id)
            order_status = json.loads(own["messages"][-1]["text"])
            self.assertEqual(order_status["order_id"], order_id)
            self.assertEqual(order_status["status"], "pending")
            for field in ("customer_name", "delivery_phone", "delivery_address"):
                self.assertNotIn(field, order_status)
            before_cancel = self.order_rows()
            cancellation = self.send(client, headers, thread, "cancel:" + order_id)
            self.assertEqual(self.order_rows(), before_cancel)
            answer = self.confirm(client, headers, cancellation)
            self.assertEqual(json.loads(answer["messages"][-1]["text"])["status"], "cancelled")
            self.assertEqual(len(self.order_rows()), len(before_cancel))
            self.assertEqual(order_repository.get_order_status(self.legacy_order)["status"], "pending")

    def test_real_image_catalog_overrides_the_old_chicken_and_pizza_assumptions(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            menu = self.send(client, headers, thread, "عندكم إيه؟")["messages"][-1]["text"]
            self.assertIn("كلاسيك برجر - 110.00", menu)
            self.assertIn("بيتزا مارغريتا - 140.00", menu)
            self.assertNotIn("برجر دجاج", menu)
            draft = self.send(client, headers, thread, "عايز برجر دجاج و2 بيبسي وبيتزا مارجريتا.")
            text = draft["messages"][-1]["text"]
            self.assertIn("برجر دجاج: مش موجود في المنيو", text)
            self.assertIn("بيتزا مارجريتا: لسه مش متأكدين إنه متاح", text)
            self.assertIn("بيبسي × 2", text)
            self.assertIsNone(draft["pending_confirmation"])
            self.send(client, headers, thread, "أيوه")
            self.assertEqual(self.order_rows(), before)

    def test_broad_burger_request_needs_choice_before_ordering_by_id(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            broad = self.send(client, headers, thread, "عايز برجر")
            text = broad["messages"][-1]["text"]
            for name in ("تشيز برجر", "كلاسيك برجر", "دبل برجر"):
                self.assertIn(name, text)
            for unrelated in ("بيبسي", "بطاطس", "مش موجود في المنيو"):
                self.assertNotIn(unrelated, text)
            self.assertIsNone(broad["pending_confirmation"])
            self.assertEqual(self.order_rows(), before)
            state = client.app.state.agent.get_state(api_module.thread_config(thread))
            self.assertEqual(
                [msg.name for msg in state.values["messages"] if msg.type == "tool"],
                ["resolve_menu_item", "update_order_draft"],
            )

            quote = self.send(client, headers, thread, "تشيز برجر")
            self.assertTrue(quote["pending_confirmation"]["editable"])
            self.assertEqual(self.order_rows(), before)
            final = self.start_final_checkout(client, headers, quote)
            self.assertEqual(self.order_rows(), before)
            order = self.confirm(client, headers, final)
            order_id = re.search(r"[0-9a-f-]{36}", order["messages"][-1]["text"]).group()
            with psycopg.connect(WEB_TEST_URL) as conn:
                item_id, price = conn.execute("SELECT id, price FROM menu_items WHERE name = %s", ("تشيز برجر",)).fetchone()
                lines = conn.execute("""
                    SELECT menu_item_id, unit_price, quantity FROM order_items WHERE order_id = %s
                """, (order_id,)).fetchall()
            self.assertEqual(lines, [(item_id, price, 1)])

    def test_category_quantity_survives_choice_and_unknown_does_not_substitute(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            broad = self.send(client, headers, thread, "عايز ٢ برجر")
            self.assertIn("كلاسيك برجر", broad["messages"][-1]["text"])
            self.assertIsNone(broad["pending_confirmation"])
            quote = self.send(client, headers, thread, "تشيز برجر")
            self.assertIn("تشيز برجر × 2", quote["messages"][-1]["text"])
            self.assertIn("إجمالي الأصناف: 250.00 جنيه", quote["messages"][-1]["text"])
            other_thread = self.conversation(client, headers)["thread_id"]
            unknown = self.send(client, headers, other_thread, "عايز سوشي")
            self.assertIn("سوشي: مش موجود في المنيو", unknown["messages"][-1]["text"])
            self.assertNotIn("تشيز برجر", unknown["messages"][-1]["text"])
            self.assertIsNone(unknown["pending_confirmation"])
            self.assertEqual(self.order_rows(), before)

    def test_remove_one_issue_preserves_valid_items_and_text_confirmation_is_idempotent(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            invalid = self.send(client, headers, thread, "عايز برجر بالجبنة و2 بيبسي وبرجر دجاج.")
            self.assertIsNone(invalid["pending_confirmation"])
            self.assertIn("برجر دجاج: مش موجود", invalid["messages"][-1]["text"])
            quote = self.send(client, headers, thread, "خلاص شيلها.")
            self.assertTrue(quote["pending_confirmation"]["editable"])
            self.assertIn("إجمالي الأصناف: 175.00 جنيه", quote["messages"][-1]["text"])
            self.assertIsNone(self.send(client, headers, thread, "أيوه")["pending_confirmation"])
            self.assertEqual(self.order_rows(), before)
            final = self.send(client, headers, thread, DELIVERY_MESSAGE)
            self.assertEqual(final["pending_confirmation"]["stage"], "final")
            self.assertEqual(self.order_rows(), before)
            confirmation_id = final["pending_confirmation"]["id"]
            order = self.send(client, headers, thread, "أكد الطلب", confirmation_id=confirmation_id)
            receipt = order["messages"][-1]["text"]
            self.assertIn("اتسجل طلبك", receipt)
            order_id = re.search(r"[0-9a-f-]{36}", receipt).group()
            self.assertEqual(self.send(client, headers, thread, "أكد الطلب", confirmation_id=confirmation_id), order)
            self.assertEqual(self.confirm(client, headers, final), order)
            self.assertEqual(len(self.order_rows()), len(before) + 1)
            with psycopg.connect(WEB_TEST_URL) as conn:
                lines = conn.execute("""
                    SELECT m.name, oi.quantity, oi.unit_price::text
                    FROM order_items oi JOIN menu_items m ON m.id = oi.menu_item_id
                    WHERE oi.order_id = %s ORDER BY oi.id
                """, (order_id,)).fetchall()
            self.assertEqual(lines, [("تشيز برجر", 1, "125.00"), ("بيبسي", 2, "25.00")])

    def test_progressive_checkout_rejects_invalid_fields_and_survives_restart(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            quote = self.send(client, headers, thread, "create")
            self.assertEqual(quote["pending_confirmation"]["stage"], "proceed")
            collecting = self.send(client, headers, thread, "أيوه")
            self.assertIsNone(collecting["pending_confirmation"])
            self.assertIn("اسم حضرتك بالكامل", collecting["messages"][-1]["text"])
            bad_name = self.send(client, headers, thread, "سلمى")
            self.assertIn("اسم حضرتك بالكامل", bad_name["messages"][-1]["text"])
            name = self.send(client, headers, thread, DELIVERY_NAME)
            self.assertNotIn("اسم حضرتك بالكامل", name["messages"][-1]["text"])
            bad_phone = self.send(client, headers, thread, "12345")
            self.assertIn("رقم موبايل مصري صحيح", bad_phone["messages"][-1]["text"])
            partial = self.send(client, headers, thread, "+20 101 234 5678")
            self.assertNotIn("رقم موبايل مصري صحيح", partial["messages"][-1]["text"])
            self.assertIn("عنوان التوصيل بالتفصيل", partial["messages"][-1]["text"])
            self.assertEqual(self.order_rows(), before)

        with TestClient(create_app()) as client:
            restored = client.get(f"/api/conversations/{thread}/messages", headers=headers)
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.json(), partial)
            bad_address = self.send(client, headers, thread, "الدقي")
            self.assertIn("عنوان التوصيل بالتفصيل", bad_address["messages"][-1]["text"])
            premature = self.send(client, headers, thread, "أكد الطلب")
            self.assertIsNone(premature["pending_confirmation"])
            self.assertEqual(self.order_rows(), before)
            final = self.send(client, headers, thread, DELIVERY_ADDRESS)
            self.assertEqual(final["pending_confirmation"]["stage"], "final")
            summary = final["messages"][-1]["text"]
            for value in (DELIVERY_NAME, DELIVERY_PHONE, DELIVERY_ADDRESS, "إجمالي الأصناف: 25.00 جنيه"):
                self.assertIn(value, summary)
            for message in final["messages"]:
                if message["role"] == "customer":
                    self.assertNotIn(DELIVERY_PHONE, message["text"])
            casual_reply = self.send(client, headers, thread, "تمام")
            self.assertEqual(casual_reply["pending_confirmation"], final["pending_confirmation"])
            self.assertEqual(self.order_rows(), before)
            receipt = self.confirm(client, headers, final)
            self.assertIn("اتسجل طلبك", receipt["messages"][-1]["text"])
            self.assertEqual(len(self.order_rows()), len(before) + 1)

    def test_final_checkout_edits_require_fresh_confirmation(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            quote = self.send(client, headers, thread, "create")
            original = self.start_final_checkout(client, headers, quote)
            new_address = "١٨ شارع التحرير، الدقي، الجيزة"
            moved = self.send(client, headers, thread, f"العنوان الجديد: {new_address}")
            self.assertEqual(moved["pending_confirmation"]["stage"], "final")
            self.assertNotEqual(moved["pending_confirmation"]["id"], original["pending_confirmation"]["id"])
            self.assertIn(new_address, moved["messages"][-1]["text"])
            self.confirm(client, headers, original, status_code=409)

            changed = self.send(client, headers, thread, "عايز ٢ بيبسي")
            self.assertEqual(changed["pending_confirmation"]["stage"], "final")
            self.assertNotEqual(changed["pending_confirmation"]["id"], moved["pending_confirmation"]["id"])
            self.assertIn("بيبسي × 2", changed["messages"][-1]["text"])
            self.assertIn("إجمالي الأصناف: 50.00 جنيه", changed["messages"][-1]["text"])
            self.confirm(client, headers, moved, status_code=409)
            self.send(client, headers, thread, "أكد الطلب", status_code=409)
            self.send(
                client, headers, thread, "أكد الطلب",
                confirmation_id=moved["pending_confirmation"]["id"], status_code=409,
            )
            self.assertEqual(self.order_rows(), before)

            receipt = self.confirm(client, headers, changed)
            order_id = re.search(r"[0-9a-f-]{36}", receipt["messages"][-1]["text"]).group()
            with psycopg.connect(WEB_TEST_URL) as conn:
                stored = conn.execute("""
                    SELECT o.delivery_address, o.total_amount::text, oi.quantity
                    FROM orders o JOIN order_items oi ON oi.order_id = o.id WHERE o.id = %s
                """, (order_id,)).fetchone()
            self.assertEqual(stored, (new_address, "50.00", 2))

    def test_cancelling_final_checkout_invalidates_its_confirmation(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            quote = self.send(client, headers, thread, "create")
            final = self.start_final_checkout(client, headers, quote)
            cancelled = self.send(client, headers, thread, "الغي الطلب")
            self.assertIn("ألغينا مسودة الطلب", cancelled["messages"][-1]["text"])
            self.assertIsNone(cancelled["pending_confirmation"])
            self.confirm(client, headers, final, status_code=409)
            self.assertEqual(self.order_rows(), before)

    def test_delivery_details_are_hidden_from_subsequent_model_requests(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            quote = self.send(client, headers, thread, "create")
            self.start_final_checkout(client, headers, quote)
            response = self.send(client, headers, thread, "inspect checkout context")
            serialized = response["messages"][-1]["text"]
            for private in (DELIVERY_NAME, DELIVERY_PHONE, DELIVERY_ADDRESS, "sofra_checkout"):
                self.assertNotIn(private, serialized)
            self.assertIn("inspect checkout context", serialized)

    def test_unavailable_alias_is_not_added_and_a_changed_price_blocks_confirmation(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            unavailable = self.send(client, headers, thread, "بطاطس")
            self.assertIn("fries: مش متاح دلوقتي", unavailable["messages"][-1]["text"])
            self.assertIsNone(unavailable["pending_confirmation"])
            thread = self.conversation(client, headers)["thread_id"]
            quote = self.send(client, headers, thread, "create")
            final = self.start_final_checkout(client, headers, quote)
            with psycopg.connect(WEB_TEST_URL) as conn:
                conn.execute("UPDATE menu_items SET price = price + 1 WHERE name = %s", ("بيبسي",))
            try:
                result = self.confirm(client, headers, final)
                self.assertIn("ما اتسجلش", result["messages"][-1]["text"])
                self.assertIn("إجمالي الأصناف: 26.00 جنيه", result["messages"][-1]["text"])
                self.assertEqual(result["pending_confirmation"]["stage"], "final")
                self.assertNotEqual(result["pending_confirmation"]["id"], final["pending_confirmation"]["id"])
                self.confirm(client, headers, final, status_code=409)
                self.assertEqual(self.order_rows(), before)
            finally:
                with psycopg.connect(WEB_TEST_URL) as conn:
                    conn.execute("UPDATE menu_items SET price = price - 1 WHERE name = %s", ("بيبسي",))

    def test_rejecting_proposal_does_not_create_order(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            before = self.order_rows()
            proposal = self.send(client, headers, thread, "create")
            result = self.confirm(client, headers, proposal, decision="reject")
            self.assertIsNone(result["pending_confirmation"])
            self.assertEqual(self.order_rows(), before)

    def test_menu_image_survives_history_and_is_served_unchanged(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            view = self.send(client, headers, thread, "menu")
            self.assertEqual(view["messages"][-1]["images"], ["/api/menu/image"])
            history = client.get(f"/api/conversations/{thread}/messages", headers=headers).json()
            self.assertEqual(history, view)
            file_response = client.get("/api/menu/image")
            self.assertEqual(file_response.headers["content-type"], "image/png")
            self.assertTrue(file_response.content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_concurrent_turn_and_cors(self):
        with TestClient(create_app()) as client:
            _, headers = self.identity(client)
            thread = self.conversation(client, headers)["thread_id"]
            from uuid import UUID
            with customer_repository.conversation_lock(UUID(thread)) as acquired:
                self.assertTrue(acquired)
                response = client.post(f"/api/conversations/{thread}/messages", headers=headers, json={"message": "busy"})
                self.assertEqual(response.status_code, 409)
            preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
            good = client.options("/api/conversations", headers={**preflight, "Origin": "http://127.0.0.1:5173"})
            self.assertEqual(good.headers["access-control-allow-origin"], "http://127.0.0.1:5173")
            bad = client.options("/api/conversations", headers={**preflight, "Origin": "https://untrusted.example"})
            self.assertNotIn("access-control-allow-origin", bad.headers)
