"""Server-owned checkout transitions stored with the current LangGraph quote."""

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langsmith import tracing_context

from app.database.menu_repository import get_all_menu_items, normalize_item_name
from app.database.order_repository import create_order, get_confirmed_order
from app.delivery import DeliveryExtractionError, check_delivery_fields, validate_delivery_details
from app.order_draft import build_draft, draft_details, draft_text, latest_draft, quote_was_last_turn


_PROCEED = {"ايوه", "ايوا", "اه", "نعم", "تمام", "موافق", "كمل", "كمل الطلب", "yes", "proceed"}
_EXPLICIT_PROCEED = {"كمل", "كمل الطلب", "proceed"}
_FINAL_APPROVAL = {"اكد", "اكد الطلب", "اكد الاوردر", "اكد الاوردر ده", "اكد الطلب نهائيا", "تاكيد الطلب", "confirm", "confirm order"}
_CANCEL = {"الغي", "الغاء", "الغي الطلب", "الغاء الطلب", "الغي الاوردر", "عايز الغي الطلب", "مش هكمل", "مش عايز اكمل", "لا", "لا شكرا", "كنسل", "cancel checkout", "cancel order"}
_FIELD_REQUESTS = {
    "full_name": "اسم حضرتك بالكامل",
    "phone_number": "رقم موبايل مصري صحيح زي 01XXXXXXXXX",
    "address": "عنوان التوصيل بالتفصيل، زي رقم العمارة والشارع والمنطقة",
}


def is_final_approval(text):
    return normalize_item_name(text) in _FINAL_APPROVAL


def record_order_reply(agent, config, text, *, user_message=None, private_input=False, **metadata):
    messages = []
    if user_message is not None:
        if private_input:
            messages.append(HumanMessage(content="بيانات توصيل", additional_kwargs={"sofra_delivery_input": user_message}))
        else:
            messages.append(HumanMessage(content=user_message))
    messages.append(AIMessage(content=text, additional_kwargs=metadata))
    with tracing_context(enabled=False):
        agent.update_state(config, {"messages": messages}, as_node="model")


def checkout_for_draft(messages, draft):
    if not draft:
        return None
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if message.additional_kwargs.get("sofra_rejected_draft"):
            return None
        checkout = message.additional_kwargs.get("sofra_checkout")
        if checkout:
            if checkout["draft_id"] == draft["id"]:
                return checkout
            if checkout["stage"] != "complete":
                # A changed quote can carry its own delivery details, but its old token expires.
                return {"draft_id": draft["id"], "stage": "review", "details": checkout["details"]}
            return None
    return None


def placement_id(draft, checkout):
    # Older receipts used the quote ID as their idempotency key.
    return (checkout or {}).get("confirmation_id") or draft["id"]


def draft_is_ready(draft):
    return bool(draft and draft["items"] and not draft["issues"])


def checkout_summary(details, draft):
    return "\n".join([
        "راجع ملخص طلب التوصيل",
        f"الاسم: {details['full_name']}",
        f"الموبايل: {details['phone_number']}",
        f"عنوان التوصيل: {details['address']}",
        *draft_details(draft),
        f"إجمالي الأصناف: {draft['total_amount']} جنيه.",
        "رسوم التوصيل لسه مش محددة ومش داخلة في إجمالي الأصناف.",
        "لو البيانات والأصناف مظبوطة، قول «أكد الطلب» أو اضغط «أكد الطلب».",
    ])


def checkout_reply_text(message):
    summary = message.additional_kwargs.get("sofra_checkout_summary")
    if summary:
        checkout = message.additional_kwargs["sofra_checkout"]
        text = checkout_summary(checkout["details"], summary)
        return summary["notice"] + "\n" + text if summary["notice"] else text
    return str(message.text)


def publish_checkout(agent, config, draft, details, *, previous=None, user_message=None, private_input=False, notice=None, changed_draft=False):
    details, missing = check_delivery_fields(details)
    checkout = {"draft_id": draft["id"], "stage": "collecting", "details": details}
    metadata = {"sofra_checkout": checkout}
    if changed_draft:
        metadata["sofra_order_draft"] = draft
    if not draft_is_ready(draft):
        text = draft_text(draft)
    elif missing:
        text = "محتاج منك " + "، و".join(_FIELD_REQUESTS[field] for field in missing) + "."
        if len(missing) > 1:
            text += " ممكن تبعت البيانات في رسالة واحدة أو واحدة واحدة."
    else:
        checkout["stage"] = "final"
        if previous and previous.get("stage") == "final" and previous["draft_id"] == draft["id"] and previous["details"] == details:
            checkout["confirmation_id"] = previous["confirmation_id"]
        else:
            checkout["confirmation_id"] = str(uuid4())
        # The authenticated chat renders this privately. The model sees only the generic text.
        metadata["sofra_checkout_summary"] = {
            "items": draft["items"], "total_amount": draft["total_amount"], "notice": notice,
        }
        text = "ملخص التوصيل جاهز للتأكيد النهائي."
    if notice and checkout["stage"] != "final":
        text = notice + "\n" + text
    record_order_reply(agent, config, text, user_message=user_message, private_input=private_input, **metadata)


def start_checkout(agent, config, draft, checkout, *, user_message=None):
    if not draft_is_ready(draft):
        record_order_reply(agent, config, draft_text(draft), user_message=user_message)
        return
    publish_checkout(agent, config, draft, (checkout or {}).get("details", {}), user_message=user_message)


def confirm_checkout(agent, config, draft, checkout, customer_id, *, user_message=None):
    if not checkout or checkout["stage"] != "final" or not draft_is_ready(draft):
        raise ValueError("Final checkout confirmation is not pending")
    delivery = validate_delivery_details(checkout["details"])
    try:
        order = create_order(
            delivery["full_name"], draft["items"], customer_id=customer_id,
            delivery_phone=delivery["phone_number"], delivery_address=delivery["address"],
            confirmation_id=checkout["confirmation_id"],
        )
    except ValueError:
        refreshed = build_draft(get_all_menu_items(), draft, [], drop_single_issue=False)
        publish_checkout(
            agent, config, refreshed, delivery, user_message=user_message, changed_draft=True,
            notice="الطلب لسه ما اتسجلش. راجع الأصناف والأسعار المحدثة قبل التأكيد.",
        )
        return
    record_receipt(agent, config, draft, checkout, order, user_message=user_message)


def record_receipt(agent, config, draft, checkout, order, *, user_message=None):
    receipt = {
        "draft_id": draft["id"], "stage": "complete", "confirmation_id": checkout["confirmation_id"],
    }
    record_order_reply(
        agent, config, f"اتسجل طلبك. رقم الطلب: {order['order_id']}. إجمالي الأصناف: {order['total_amount']} جنيه.",
        user_message=user_message, sofra_checkout=receipt, sofra_confirmed_draft=draft["id"],
    )


def cancel_checkout(agent, config, draft, *, user_message=None):
    record_order_reply(
        agent, config, "تمام، ألغينا مسودة الطلب.", user_message=user_message, sofra_rejected_draft=draft["id"],
    )


def handle_checkout_message(agent, config, messages, customer_id, text, *, extract_delivery):
    """Handle approvals and private delivery input before calling the model."""
    draft = latest_draft(messages)
    checkout = checkout_for_draft(messages, draft)
    approval = normalize_item_name(text)
    final_approval = approval in _FINAL_APPROVAL
    is_approval = final_approval or approval in _PROCEED
    if checkout and checkout["stage"] == "complete" and is_approval:
        return True
    is_final = bool(checkout and checkout["stage"] == "final")
    saved = get_confirmed_order(checkout["confirmation_id"], customer_id) if is_final else None
    if saved:
        record_receipt(agent, config, draft, checkout, saved, user_message=text if is_approval else None)
        if is_approval or approval in _CANCEL:
            return is_approval
    if draft and (is_approval or approval in _CANCEL):
        if not is_final and get_confirmed_order(placement_id(draft, checkout), customer_id):
            return is_approval
        if approval in _CANCEL:
            cancel_checkout(agent, config, draft, user_message=text)
            return True
        if checkout and checkout["stage"] == "final":
            if final_approval:
                confirm_checkout(agent, config, draft, checkout, customer_id, user_message=text)
            else:
                record_order_reply(agent, config, "لو راجعت الملخص، قول «أكد الطلب» عشان نسجله.", user_message=text)
            return True
        if checkout or final_approval or approval in _EXPLICIT_PROCEED or quote_was_last_turn(messages, draft["id"]):
            start_checkout(agent, config, draft, checkout, user_message=text)
            return True

    if not checkout or checkout["stage"] not in {"collecting", "final"}:
        return False
    if approval in {"المنيو", "منيو", "صوره المنيو", "عندكم ايه"}:
        return False

    _, missing = check_delivery_fields(checkout["details"])
    try:
        updates = extract_delivery(text, missing)
    except DeliveryExtractionError:
        if not saved:
            record_order_reply(
                agent, config, "مقدرتش أراجع بيانات التوصيل دلوقتي. ابعت الرسالة تاني.",
                user_message=text, private_input=True,
                sofra_checkout={"draft_id": draft["id"], "stage": "collecting", "details": checkout["details"]},
            )
        return True
    if saved:
        return bool(updates)
    if updates:
        publish_checkout(
            agent, config, draft, {**checkout["details"], **updates}, previous=checkout, user_message=text, private_input=True,
        )
        return True
    return False


def refresh_checkout_after_edit(agent, config, previous, messages, customer_id):
    draft = latest_draft(messages)
    if not previous or previous["stage"] == "complete" or not draft or draft["id"] == previous["draft_id"]:
        return
    if previous.get("confirmation_id") and get_confirmed_order(previous["confirmation_id"], customer_id):
        return
    publish_checkout(agent, config, draft, previous["details"])
