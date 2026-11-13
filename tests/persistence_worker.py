"""Run by the integration test in TWO separate Python processes. No OpenAI calls."""
import os
import sys
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent import runner
from app.agent import checkpointer


class TestChatModel(BaseChatModel):
    """Use the real LangChain agent with a deterministic, offline chat model."""

    @property
    def _llm_type(self):
        return "offline-persistence-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        users = [message.content for message in messages if message.type == "human"]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="|".join(users)))])


def main():
    # The parent validated this dedicated URL; never read the app's real .env URL.
    checkpointer.DB_URL = os.environ["RESTAURANT_TEST_DATABASE_URL"]
    mode, thread_id = sys.argv[1:]
    with (
        patch.object(runner, "ChatOpenAI", return_value=TestChatModel()),
        patch.object(runner, "create_delivery_extractor"),
    ):
        with runner.create_agent_runner() as run:
            if mode == "write":
                assert run(thread_id, "remember-test") == "remember-test"
            else:
                assert run(thread_id, "after-restart") == "remember-test|after-restart"
                assert run(thread_id + "-other", "separate") == "separate"
                try:
                    run(" ", "invalid-thread")
                except ValueError:
                    pass
                else:
                    raise AssertionError("Blank conversation id was accepted")
    print("Persistence worker passed:", mode)


if __name__ == "__main__":
    main()
