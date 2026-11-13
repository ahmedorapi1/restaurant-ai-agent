"""An offline chat model that exercises the real tool and checkpoint pipeline."""

import json
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class OfflineRestaurantModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "offline-restaurant-test"

    def bind_tools(self, tools, **kwargs):
        return self.bind(**kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        last = messages[-1]
        text = next(str(message.text) for message in reversed(messages) if message.type == "human")
        if kwargs.get("tool_choice") == "resolve_menu_item":
            return self.call_tool("resolve_menu_item", {"item_name": text.split(" و", 1)[0]})
        if isinstance(last, ToolMessage) and last.name != "resolve_menu_item":
            result = "اتفضل يا فندم، دي صورة المنيو." if last.name == "get_menu_image" else last.text
            return self.reply(str(result))

        if "صورة المنيو" in text or text == "menu":
            return self.call_tool("get_menu_image", {})
        if text == "create":
            return self.draft(("pepsi", 1))
        if text == "عايز برجر":
            # Deliberately pick a product so the server must preserve the category.
            return self.draft(("تشيز برجر", 1))
        if text == "عايز ٢ برجر":
            return self.draft()
        if text == "عايز ٢ بيبسي":
            return self.draft(("pepsi", 2))
        if text == "عايز سوشي":
            return self.draft(("تشيز برجر", 1))
        if text == "تشيز برجر":
            return self.draft(("تشيز برجر", 1))
        if text == "عايز برجر دجاج و2 بيبسي وبيتزا مارجريتا.":
            return self.draft(("برجر دجاج", 1), ("بيبسي", 2), ("بيتزا مارجريتا", 1))
        if text == "عايز برجر بالجبنة و2 بيبسي وبرجر دجاج.":
            return self.draft(("برجر بالجبنة", 1), ("بيبسي", 2), ("برجر دجاج", 1))
        if text == "خلاص شيلها.":
            return self.call_tool("update_order_draft", {"edits": [], "drop_single_issue": True})
        if text in {"بطاطس", "عايز بطاطس"}:
            return self.draft(("fries", 1))
        if text.startswith("status:"):
            return self.call_tool("check_order_status", {"order_id": text.split(":", 1)[1]})
        if text.startswith("cancel:"):
            return self.call_tool("cancel_1order", {"order_id": text.split(":", 1)[1]})
        if text == "profile":
            prompt = next(str(message.text) for message in messages if message.type == "system")
            profile = json.loads(prompt.split("Customer profile data: ", 1)[1])
            return self.reply(profile["full_name"] + " | " + profile["food_preferences"])
        if text == "inspect checkout context":
            return self.reply(json.dumps([message.model_dump(mode="json") for message in messages], ensure_ascii=False))
        if "الأصناف" in text or text == "عندكم إيه؟":
            return self.call_tool("get_all_items_of_the_menu", {})
        if "حالة طلبي" in text:
            return self.reply("أكيد يا فندم، ممكن تبعتلي رقم الطلب؟")
        return self.reply(" | ".join(str(message.text) for message in messages if message.type == "human"))

    @staticmethod
    def reply(text):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def draft(self, *items):
        return self.call_tool("update_order_draft", {
            "edits": [
                {"operation": "set", "item_name": name, "quantity": quantity}
                for name, quantity in items
            ],
        })

    @staticmethod
    def call_tool(name, arguments):
        message = AIMessage(content="", tool_calls=[{"name": name, "args": arguments, "id": str(uuid4())}])
        return ChatResult(generations=[ChatGeneration(message=message)])
