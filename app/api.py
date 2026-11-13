import logging
import os
from contextlib import ExitStack, asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.types import Command
from langsmith import tracing_context
from openai import OpenAIError
from pydantic import BaseModel, ConfigDict, Field

from app.agent.menu_image import IMAGE_TYPES, local_menu_file
from app.agent.runner import create_restaurant_agent, create_restaurant_model
from app.database import customer_repository as customers
from app.database.menu_repository import get_all_menu_items, normalize_item_name
from app.database.order_repository import get_confirmed_order
from app.delivery import create_delivery_extractor
from app.delivery_checkout import (
    cancel_checkout, checkout_for_draft, confirm_checkout, draft_is_ready, handle_checkout_message, is_final_approval,
    placement_id, record_order_reply, refresh_checkout_after_edit, start_checkout,
)
from app.order_draft import build_draft, draft_text, latest_draft, quote_was_last_turn
from app.web_chat import customer_context, pending_confirmation, thread_config, web_conversation_view

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


class CustomerProfileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    full_name: str = Field(min_length=1, max_length=100)
    address: str | None = Field(default=None, max_length=300)
    food_preferences: str = Field(default="", max_length=400)
    disliked_foods: str = Field(default="", max_length=400)
    dietary_preferences: str = Field(default="", max_length=400)
    profile_summary: str = Field(default="", max_length=500)


class MessageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    confirmation_id: UUID | None = None


class ConfirmationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "reject"]


def current_customer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict:
    if credentials and len(credentials.credentials) == 43:
        profile = customers.get_demo_customer(credentials.credentials)
        if profile:
            return profile
    raise HTTPException(401, "Demo session required", headers={"WWW-Authenticate": "Bearer"})


Customer = Annotated[dict, Depends(current_customer)]


def require_owner(thread_id: UUID, profile: dict) -> None:
    if not customers.owns_conversation(thread_id, profile["id"]):
        raise HTTPException(404, "Conversation not found")


def run_turn(agent, payload, config, profile):
    try:
        with tracing_context(enabled=False):
            agent.invoke(payload, config=config, context=customer_context(profile))
    except psycopg.Error:
        raise
    except Exception as error:
        logger.warning("Agent request failed (%s)", type(error).__name__)
        raise HTTPException(502, "Assistant temporarily unavailable") from None


_REMOVE_SINGLE_ISSUE = {"شيلها", "خلاص شيلها", "شيله", "خلاص شيله", "احذفها", "remove it"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    with ExitStack() as stack:
        try:
            customers.check_web_schema()
            model = create_restaurant_model(web=True)
            app.state.agent = stack.enter_context(create_restaurant_agent(web=True, model=model))
            app.state.extract_delivery = create_delivery_extractor(model)
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
            raise RuntimeError(
                "Restaurant web schema is incomplete. Follow the database setup and migration in README.md."
            ) from None
        except psycopg.OperationalError as error:
            raise RuntimeError(
                f"PostgreSQL connection failed ({type(error).__name__}). Check DATABASE_URL and start PostgreSQL."
            ) from None
        except psycopg.Error as error:
            raise RuntimeError(f"Restaurant database startup failed ({type(error).__name__}).") from None
        except (OpenAIError, ValueError) as error:
            raise RuntimeError(
                f"Assistant startup failed ({type(error).__name__}). Check OPENAI_API_KEY and OPENAI_MODEL."
            ) from None
        yield


def create_app() -> FastAPI:
    api = FastAPI(title="Restaurant Chat API", lifespan=lifespan)
    origins = os.getenv("FRONTEND_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in origins.split(",") if origin.strip() and origin.strip() != "*"],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @api.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/") and request.url.path != "/api/menu/image":
            response.headers["Cache-Control"] = "no-store"
        return response

    @api.exception_handler(psycopg.Error)
    async def database_error(_request: Request, error: psycopg.Error):
        logger.warning("Restaurant API database error (%s)", type(error).__name__)
        return JSONResponse({"detail": "Database temporarily unavailable"}, status_code=503)

    @api.get("/health")
    def health():
        with customers.connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @api.post("/api/customers/demo", status_code=201)
    def start_demo():
        token, profile = customers.create_demo_customer()
        return {"token": token, "profile": profile}

    @api.get("/api/customers/me")
    def read_profile(profile: Customer):
        return profile

    @api.put("/api/customers/me")
    def save_profile(body: CustomerProfileInput, profile: Customer):
        return customers.update_profile(profile["id"], body.model_dump())

    @api.post("/api/conversations", status_code=201)
    def new_conversation(profile: Customer):
        thread_id = customers.create_conversation(profile["id"])
        return {"thread_id": str(thread_id), "messages": [], "pending_confirmation": None}

    @api.get("/api/conversations/{thread_id}/messages")
    def get_messages(thread_id: UUID, profile: Customer, request: Request):
        require_owner(thread_id, profile)
        snapshot = request.app.state.agent.get_state(thread_config(thread_id))
        return web_conversation_view(snapshot, thread_id, profile["id"])

    @api.post("/api/conversations/{thread_id}/messages")
    def send_message(thread_id: UUID, body: MessageInput, profile: Customer, request: Request):
        require_owner(thread_id, profile)
        agent = request.app.state.agent
        config = thread_config(thread_id)
        with customers.conversation_lock(thread_id) as acquired:
            if not acquired:
                raise HTTPException(409, "Conversation is already processing")
            snapshot = agent.get_state(config)
            if snapshot.interrupts:
                raise HTTPException(409, "Resolve the pending confirmation first")
            messages = snapshot.values.get("messages", [])
            draft = latest_draft(messages)
            checkout = checkout_for_draft(messages, draft)
            if (
                checkout and checkout["stage"] == "final" and is_final_approval(body.message)
                and str(body.confirmation_id) != checkout["confirmation_id"]
            ):
                raise HTTPException(409, "Review the current checkout summary before confirming")
            if handle_checkout_message(
                agent, config, messages, profile["id"], body.message, extract_delivery=request.app.state.extract_delivery,
            ):
                return web_conversation_view(agent.get_state(config), thread_id, profile["id"])
            if (
                draft and len(draft["issues"]) == 1 and normalize_item_name(body.message) in _REMOVE_SINGLE_ISSUE
                and quote_was_last_turn(messages, draft["id"])
                and not get_confirmed_order(placement_id(draft, checkout), profile["id"])
            ):
                edited = build_draft(get_all_menu_items(), draft, [], drop_single_issue=True)
                record_order_reply(
                    agent, config, draft_text(edited), user_message=body.message, sofra_order_draft=edited,
                )
            else:
                run_turn(agent, {"messages": [{"role": "user", "content": body.message}]}, config, profile)
            updated = agent.get_state(config)
            if checkout and not updated.interrupts:
                refresh_checkout_after_edit(agent, config, checkout, updated.values.get("messages", []), profile["id"])
                updated = agent.get_state(config)
            return web_conversation_view(updated, thread_id, profile["id"])

    @api.post("/api/conversations/{thread_id}/confirmation")
    def confirm_action(thread_id: UUID, body: ConfirmationInput, profile: Customer, request: Request):
        require_owner(thread_id, profile)
        agent = request.app.state.agent
        config = thread_config(thread_id)
        with customers.conversation_lock(thread_id) as acquired:
            if not acquired:
                raise HTTPException(409, "Conversation is already processing")
            snapshot = agent.get_state(config)
            pending = pending_confirmation(snapshot)
            if pending:
                if pending["id"] != body.confirmation_id or any(action["kind"] == "create" for action in pending["actions"]):
                    raise HTTPException(409, "Confirmation is no longer pending")
                decisions = [{"type": body.decision} for _ in pending["actions"]]
                run_turn(agent, Command(resume={"decisions": decisions}), config, profile)
            else:
                messages = snapshot.values.get("messages", [])
                draft = latest_draft(messages)
                if not draft_is_ready(draft):
                    raise HTTPException(409, "Confirmation is no longer pending")
                checkout = checkout_for_draft(messages, draft)
                stage = checkout["stage"] if checkout else "review"
                final_id = (checkout or {}).get("confirmation_id")
                if stage == "final" and body.confirmation_id == final_id:
                    if body.decision == "approve":
                        confirm_checkout(agent, config, draft, checkout, profile["id"])
                    elif not get_confirmed_order(final_id, profile["id"]):
                        cancel_checkout(agent, config, draft)
                    else:
                        raise HTTPException(409, "Order is already saved")
                elif stage == "review" and body.confirmation_id == draft["id"]:
                    if get_confirmed_order(draft["id"], profile["id"]):
                        if body.decision == "reject":
                            raise HTTPException(409, "Order is already saved")
                    elif body.decision == "approve":
                        start_checkout(agent, config, draft, checkout)
                    else:
                        cancel_checkout(agent, config, draft)
                elif body.decision == "approve" and (
                    body.confirmation_id == draft["id"] or stage == "complete" and body.confirmation_id == final_id
                ):
                    # A retry of the initial approval never acts as final confirmation.
                    return web_conversation_view(snapshot, thread_id, profile["id"])
                else:
                    raise HTTPException(409, "Confirmation is no longer pending")
            return web_conversation_view(agent.get_state(config), thread_id, profile["id"])

    @api.get("/api/menu/image")
    def menu_image():
        path = local_menu_file()
        if path is None:
            raise HTTPException(404, "Menu image not configured")
        return FileResponse(path, media_type=IMAGE_TYPES[path.suffix.lower()],
                            headers={"X-Content-Type-Options": "nosniff"})

    return api


app = create_app()
