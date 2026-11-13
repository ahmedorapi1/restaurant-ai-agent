"""Build an order quote from the catalog and keep it in the conversation checkpoint."""

import json
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field, model_validator

from app.database.menu_repository import normalize_item_name, normalize_menu_query, search_menu_items


class DraftEdit(BaseModel):
    operation: Literal["set", "remove"]
    item_name: str | None = Field(default=None, min_length=1, max_length=100)
    menu_item_id: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647)
    quantity: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647)

    @model_validator(mode="after")
    def validate_edit(self):
        if not self.menu_item_id and not (self.item_name and self.item_name.strip()):
            raise ValueError("Provide an item name or menu item ID")
        if self.operation == "set" and self.quantity is None:
            raise ValueError("Provide the desired quantity")
        return self


def availability_label(value: bool | None) -> str:
    return {True: "متوفر", False: "غير متوفر", None: "التوفر غير مؤكد"}[value]


def tool_result(message, *names) -> dict | None:
    if not isinstance(message, ToolMessage) or message.name not in names or not isinstance(message.content, str):
        return None
    try:
        data = json.loads(message.content)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("success") is True else None


def latest_draft(messages) -> dict | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            if message.additional_kwargs.get("sofra_rejected_draft"):
                return None
            if draft := message.additional_kwargs.get("sofra_order_draft"):
                return draft
        data = tool_result(message, "update_order_draft")
        if data:
            return data["draft"]
    return None


def quote_was_last_turn(messages, draft_id: str) -> bool:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return False
        if isinstance(message, AIMessage):
            if message.additional_kwargs.get("sofra_confirmed_draft") == draft_id:
                return True
            if (message.additional_kwargs.get("sofra_order_draft") or {}).get("id") == draft_id:
                return True
        data = tool_result(message, "update_order_draft", "get_order_draft")
        if data and data.get("draft") and data["draft"]["id"] == draft_id:
            return True
    return False


def explicit_order_request(message: str, *, has_draft: bool) -> bool:
    """Require a catalog check for clear order/edit requests."""
    text = normalize_item_name(message)
    for prefix in ("لو سمحت ", "من فضلك ", "خلاص "):
        text = text.removeprefix(prefix)
    verb, _, rest = text.partition(" ")
    if verb.startswith(("شيل", "احذف", "بدل", "غير", "عدل", "حدث")) or verb in {"remove", "replace", "edit"}:
        return has_draft and rest not in {"الطلب كله", "الاوردر كله"}
    if verb in {"هات", "ضيف", "زود", "اطلب", "اضف", "add", "order"}:
        return bool(rest)
    if verb not in {"عايز", "عاوز", "عايزه", "عاوزه", "اريد", "حابب", "محتاج"}:
        return text.startswith(("i want ", "i d like to order "))
    first_word = rest.split(" ", 1)[0]
    return bool(rest) and first_word not in {
        "اعرف", "اشوف", "اتابع", "استعلم", "الغي", "اسال", "استفسر", "المنيو",
        "منيو", "صوره", "سعر", "اسعار", "القائمه", "قائمه", "اتكلم", "اكلم", "اتواصل", "مساعده", "مساعدتك",
    }


def selected_draft_issue(message: str, draft: dict | None) -> dict | None:
    query = normalize_menu_query(message)
    return next((
        issue
        for issue in (draft or {}).get("issues", [])
        if any(query == normalize_item_name(option["name"]) for option in issue.get("suggestions", []))
    ), None)


def build_draft(menu: list[dict], previous: dict | None, edits: list[DraftEdit], *, drop_single_issue: bool) -> dict:
    by_id = {row["id"]: row for row in menu if row.get("is_listed", True)}
    quantities = {line["menu_item_id"]: line["quantity"] for line in (previous or {}).get("items", [])}
    previous_names = {line["menu_item_id"]: line["name"] for line in (previous or {}).get("items", [])}
    issues = {normalize_menu_query(issue["request"]): issue for issue in (previous or {}).get("issues", [])}
    issues.pop(normalize_item_name("الإجمالي"), None)
    if drop_single_issue and len(issues) == 1:
        issues.clear()
    for key, issue in issues.items():
        if issue["reason"] in {"missing", "ambiguous"}:
            resolution = search_menu_items(menu, issue["request"])
            if resolution["status"] != "matched":
                suggestions = resolution["suggestions"]
                issues[key] = {**issue, "reason": "ambiguous" if suggestions else "missing", "suggestions": suggestions}

    for edit in edits:
        label = edit.item_name.strip() if edit.item_name else f"#{edit.menu_item_id}"
        key = normalize_menu_query(label)
        if edit.operation == "remove" and issues.pop(key, None) is not None:
            continue
        suggestions = []
        if edit.item_name:
            resolution = search_menu_items(menu, label)
            found = resolution.get("item")
            item = by_id[found["menu_item_id"]] if found and edit.menu_item_id in (None, found["menu_item_id"]) else None
            suggestions = resolution["suggestions"]
        else:
            item = by_id.get(edit.menu_item_id)

        if edit.operation == "remove":
            if item:
                quantities.pop(item["id"], None)
                for issue_key in list(issues):
                    if issues[issue_key].get("menu_item_id") == item["id"]:
                        del issues[issue_key]
            continue

        if not item:
            issues[key] = {
                "request": label, "reason": "ambiguous" if suggestions else "missing",
                "quantity": edit.quantity, "suggestions": suggestions,
            }
            continue

        item_id = item["id"]
        selection = next((
            issue_key for issue_key, issue in issues.items()
            if any(option["menu_item_id"] == item_id for option in issue.get("suggestions", []))
        ), None)
        if selection is not None:
            issues.pop(selection)
        if item["is_available"] is not True:
            quantities.pop(item_id, None)
            issues[key] = {"request": label, "reason": availability_label(item["is_available"]), "menu_item_id": item_id}
            continue
        issues.pop(key, None)
        for issue_key in list(issues):
            if issues[issue_key].get("menu_item_id") == item_id:
                del issues[issue_key]
        quantities[item_id] = edit.quantity

    lines = []
    total = Decimal("0.00")
    for item_id, quantity in quantities.items():
        item = by_id.get(item_id)
        if not item or item["is_available"] is not True:
            label = item["name"] if item else previous_names.get(item_id, f"#{item_id}")
            issues[normalize_item_name(label)] = {
                "request": label,
                "reason": availability_label(item["is_available"]) if item else "missing",
                "menu_item_id": item_id,
            }
            continue
        price = item["price"]
        total += price * quantity
        lines.append({"menu_item_id": item_id, "name": item["name"], "quantity": quantity, "unit_price": str(price)})

    if total > Decimal("99999999.99"):
        issues["total"] = {"request": "الإجمالي", "reason": "تجاوز الحد المسموح للطلب"}
    alternatives = dict.fromkeys(
        option["name"] for issue in issues.values() for option in issue.get("suggestions", [])
        if option["is_available"] is True and option["menu_item_id"] not in quantities
    )
    quote = {
        "items": lines,
        "issues": list(issues.values()),
        "total_amount": str(total),
        "alternatives": list(alternatives),
    }
    if previous and all(previous.get(key) == value for key, value in quote.items()):
        return previous
    return {"id": str(uuid4()), **quote}


def draft_details(draft: dict) -> list[str]:
    return [
        f"{line['name']} × {line['quantity']} — {line['unit_price']} جنيه للوحدة"
        for line in draft["items"]
    ]


def draft_text(draft: dict) -> str:
    reasons = {
        "missing": "مش موجود في المنيو",
        "ambiguous": "عندنا الاختيارات دي",
        "غير متوفر": "مش متاح دلوقتي",
        "التوفر غير مؤكد": "لسه مش متأكدين إنه متاح",
    }
    lines = []
    has_choices = False
    for issue in draft["issues"]:
        lines.append(f"{issue['request']}: {reasons.get(issue['reason'], issue['reason'])}")
        for option in issue.get("suggestions", []):
            has_choices = True
            availability = availability_label(option["is_available"])
            lines.append(f"• {option['name']} — {option['price']} جنيه — {reasons.get(availability, availability)}")
    if draft["items"]:
        lines.extend(draft_details(draft))
        label = "إجمالي الأصناف المتاحة" if draft["issues"] else "إجمالي الأصناف"
        lines.append(f"{label}: {draft['total_amount']} جنيه.")
    if has_choices:
        lines.append("اختار اسم الصنف اللي تحبه، أو قول لي تشيل أنهي صنف من الطلب.")
    elif draft["issues"]:
        lines.append("تحب تشيل أنهي صنف أو تبدّله بإيه قبل ما نأكد الطلب؟")
    elif draft["items"]:
        lines.append("رسوم التوصيل لسه مش محددة ومش داخلة في إجمالي الأصناف.")
        lines.append("لو الأصناف تمام، قول «أيوه» أو اضغط «كمّل الطلب» عشان نكمّل بيانات التوصيل.")
    else:
        lines.append("مفيش أصناف في الطلب دلوقتي. تحب تطلب إيه؟")
    return "\n".join(lines)
