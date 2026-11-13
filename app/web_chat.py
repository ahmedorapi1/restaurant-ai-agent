"""Translate private LangGraph state into a small, public chat response."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.menu_image import valid_menu_url
from app.agent.context import CustomerContext
from app.database.order_repository import get_confirmed_order
from app.delivery_checkout import checkout_for_draft, checkout_reply_text, draft_is_ready, placement_id
from app.order_draft import draft_details, draft_text, latest_draft, tool_result


def customer_context(profile: dict) -> CustomerContext:
    return CustomerContext(
        customer_id=profile["id"],
        full_name=profile["full_name"],
        food_preferences=profile["food_preferences"],
        disliked_foods=profile["disliked_foods"],
        dietary_preferences=profile["dietary_preferences"],
        profile_summary=profile["profile_summary"],
    )


def thread_config(thread_id) -> dict:
    return {"configurable": {"thread_id": str(thread_id)}, "recursion_limit": 24}


def _image_url(message: ToolMessage) -> str | None:
    if message.name != "get_menu_image" or not isinstance(message.content, str):
        return None
    try:
        result = json.loads(message.content)
    except ValueError:
        return None
    if not isinstance(result, dict) or result.get("success") is not True:
        return None
    url = result.get("url")
    return url if isinstance(url, str) and valid_menu_url(url) else None


def pending_confirmation(snapshot) -> dict | None:
    if not snapshot.interrupts:
        return None
    interrupt = snapshot.interrupts[0]
    actions = []
    for action in interrupt.value["action_requests"]:
        args = action["args"]
        if action["name"] == "create_new_order":
            items = args.get("items")
            details = [
                f"{item.get('item_name', '')} × {item.get('quantity', '')}"
                for item in items if isinstance(item, dict)
            ] if isinstance(items, list) else []
            actions.append({"kind": "create", "details": details})
        elif action["name"] == "cancel_1order":
            actions.append({"kind": "cancel", "details": [str(args.get("order_id", ""))]})
    return {"id": interrupt.id, "actions": actions}


def conversation_view(snapshot, thread_id) -> dict:
    messages = []
    images = []
    verified_reply = None
    for index, message in enumerate(snapshot.values.get("messages", [])):
        message_id = message.id or str(index)
        if isinstance(message, HumanMessage):
            if verified_reply:
                messages.append({"id": verified_reply[0], "role": "assistant", "text": verified_reply[1], "images": images})
                verified_reply, images = None, []
            text = message.additional_kwargs.get("sofra_delivery_input", str(message.text))
            messages.append({"id": message_id, "role": "customer", "text": text, "images": []})
        elif isinstance(message, ToolMessage):
            url = _image_url(message)
            if url and url not in images:
                images.append(url)
            order = tool_result(message, "update_order_draft", "get_order_draft")
            menu = tool_result(message, "get_all_items_of_the_menu")
            if order and order.get("draft"):
                verified_reply = (message_id, draft_text(order["draft"]))
            elif menu and "items" in menu and not verified_reply:
                verified_reply = (message_id, menu["menu"])
        elif isinstance(message, AIMessage) and not message.tool_calls:
            text = verified_reply[1] if verified_reply else checkout_reply_text(message)
            if text or images:
                messages.append({"id": message_id, "role": "assistant", "text": text, "images": images})
                images = []
            verified_reply = None
    if verified_reply:
        messages.append({"id": verified_reply[0], "role": "assistant", "text": verified_reply[1], "images": images})
    elif images:
        messages.append({"id": "pending-image", "role": "assistant", "text": "", "images": images})
    return {"thread_id": str(thread_id), "messages": messages, "pending_confirmation": pending_confirmation(snapshot)}


def web_conversation_view(snapshot, thread_id, customer_id) -> dict:
    view = conversation_view(snapshot, thread_id)
    messages = snapshot.values.get("messages", [])
    draft = latest_draft(messages)
    checkout = checkout_for_draft(messages, draft)
    stage = checkout["stage"] if checkout else "review"
    if (
        not view["pending_confirmation"] and draft_is_ready(draft) and stage in {"review", "final"}
        and not get_confirmed_order(placement_id(draft, checkout), customer_id)
    ):
        view["pending_confirmation"] = {
            "id": placement_id(draft, checkout),
            "stage": "final" if stage == "final" else "proceed",
            "editable": True,
            "actions": [{
                "kind": "create",
                "details": [*draft_details(draft), f"إجمالي الأصناف: {draft['total_amount']} جنيه"],
            }],
        }
    return view
