import json
import logging
import psycopg
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from app.database.menu_repository import get_all_menu_items, normalize_menu_query, search_menu_items
from app.database.order_repository import get_confirmed_order
from app.database.order_repository import cancel_order
from app.database.order_repository import get_order_status
from pydantic import BaseModel, Field, model_validator

from app.agent.context import CustomerContext
from app.delivery_checkout import checkout_for_draft, placement_id
from app.order_draft import DraftEdit, availability_label, build_draft, explicit_order_request, latest_draft, selected_draft_issue

logger = logging.getLogger(__name__)
RestaurantToolRuntime = ToolRuntime[CustomerContext | None, dict]


class OrderItemInput(BaseModel):
    item_name: str | None = Field(default=None, min_length=1, max_length=100, description="exact menu item name")
    menu_item_id: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647, description="validated database ID from menu search")
    quantity: int = Field(strict=True, gt=0, le=2_147_483_647, description="number of units")

    @model_validator(mode="after")
    def validate_item(self):
        if self.menu_item_id is None and not (self.item_name and self.item_name.strip()):
            raise ValueError("Provide a validated menu item ID or exact item name")
        return self


def _database_error(error: psycopg.Error) -> dict:
    # Exception text can contain credentials or customer data, so log only its type.
    logger.warning("Restaurant database operation failed (%s)", type(error).__name__)
    return {
        "success": False,
        "error": "Database operation failed. The outcome may be uncertain; check order status before retrying a change.",
    }


@tool
def get_all_items_of_the_menu() -> dict:
    """Get the complete restaurant menu, including prices and availability."""
    try:
        items = get_all_menu_items()
        lines = []
        for item in items:
            status = availability_label(item["is_available"])
            lines.append(f"{item['name']} - {item['price']} جنيه - {status}")
        return {
            "success": True,
            "menu": "\n".join(lines) if lines else "المنيو فاضية حاليًا.",
            "items": [{**item, "price": str(item["price"])} for item in items],
        }
    except psycopg.Error as error:
        return _database_error(error)


@tool
def resolve_menu_item(item_name: str = Field(min_length=1, max_length=100)) -> dict:
    """Search one product or category using original customer wording, including modifiers.

    A matched item has an exact name/alias. For needs_confirmation, show all relevant
    suggestions and their availability, then ask the customer to choose. Never select
    one automatically or treat unknown availability as unavailable. not_found has no
    sufficiently relevant results. IDs and prices come from the live menu database.
    """
    try:
        return {"success": True, **search_menu_items(get_all_menu_items(), item_name)}
    except psycopg.Error as error:
        return _database_error(error)

@tool
def create_new_order(customer_name: str, items: list[OrderItemInput], runtime: RestaurantToolRuntime = None) -> dict:
    """Reject legacy direct placement. Orders go through the confirmed delivery checkout."""
    return {"success": False, "error": "Delivery details and final checkout confirmation are required."}


@tool
def update_order_draft(edits: list[DraftEdit], runtime: RestaurantToolRuntime,
                       drop_single_issue: bool = False) -> dict:
    """Prepare or edit a quote without writing an order.

    Use set to set the requested quantity of one item, and remove to remove only
    the named item or unresolved request. Keep the user's item name, including
    modifiers. Broad or fuzzy matches need a choice before adding any item.
    Use an ID only from the menu after the customer chooses, never a guessed substitute.
    For an explicit request to drop the single unavailable/missing item, set
    drop_single_issue=true. Unmentioned items stay in the quote.
    """
    try:
        messages = runtime.state["messages"]
        previous = latest_draft(messages)
        customer_id = runtime.context.customer_id if runtime.context else None
        if previous and get_confirmed_order(placement_id(previous, checkout_for_draft(messages, previous)), customer_id):
            previous = None
        menu = get_all_menu_items()
        request = next((str(message.text) for message in reversed(messages) if isinstance(message, HumanMessage)), "")
        choice = selected_draft_issue(request, previous)
        direct_request = choice or explicit_order_request(request, has_draft=previous is not None)
        if direct_request and not drop_single_issue and all(edit.operation == "set" for edit in edits):
            resolution = search_menu_items(menu, request)
            query = resolution["query"]
            if 0 < len(query) <= 100:
                named_in_request = any(
                    edit.item_name and f" {normalize_menu_query(edit.item_name)} " in f" {query} "
                    for edit in edits
                )
                # Keep a direct request even if the model leaves the edits empty or guesses a different item.
                if resolution["status"] != "not_found" or not edits or (len(edits) == 1 and not named_in_request):
                    item = resolution.get("item")
                    quantity = resolution["quantity"] or (choice or {}).get("quantity") or (edits[0].quantity if len(edits) == 1 else 1)
                    edits = [DraftEdit(operation="set", item_name=query, menu_item_id=item["menu_item_id"] if item else None, quantity=quantity)]
        return {"success": True, "draft": build_draft(
            menu, previous, edits, drop_single_issue=drop_single_issue,
        )}
    except psycopg.Error as error:
        return _database_error(error)


@tool
def get_order_draft(runtime: RestaurantToolRuntime) -> dict:
    """Get the current editable quote and unresolved items in this conversation."""
    try:
        messages = runtime.state["messages"]
        draft = latest_draft(messages)
        customer_id = runtime.context.customer_id if runtime.context else None
        if draft and get_confirmed_order(placement_id(draft, checkout_for_draft(messages, draft)), customer_id):
            draft = None
        return {"success": True, "draft": draft}
    except psycopg.Error as error:
        return _database_error(error)


@tool
def cancel_1order(order_id: str, runtime: RestaurantToolRuntime = None) -> dict:
    """Cancel a restaurant order using its UUID.
    Only pending orders can be canceled.
    """

    try:
        context = runtime.context if runtime else None
        return {"success": True, **cancel_order(order_id, customer_id=context.customer_id if context else None)}

    except ValueError as error:
        return {"success": False, "error": str(error)}
    except psycopg.Error as error:
        return _database_error(error)


@tool
def check_order_status(order_id: str, runtime: RestaurantToolRuntime = None) -> dict:
    """Get the current status and details of a restaurant order."""

    try:
        context = runtime.context if runtime else None
        order = get_order_status(order_id, customer_id=context.customer_id if context else None)
        if runtime:
            order.pop("customer_name", None)

        return {
            "success": True,
            **order
        }

    except ValueError as error:
        return {
            "success": False,
            "error": str(error)
        }
    except psycopg.Error as error:
        return _database_error(error)


def _validation_error(_error) -> str:
    # LangChain requires text here; ToolMessage content uses the same JSON format.
    return json.dumps({"success": False, "error": "Invalid tool input. Check required fields and positive integer quantities."})


tools = [get_all_items_of_the_menu, resolve_menu_item, update_order_draft, get_order_draft, cancel_1order, check_order_status]
for restaurant_tool in [*tools, create_new_order]:
    restaurant_tool.handle_validation_error = _validation_error
