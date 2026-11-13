"""Tests never use the application's DATABASE_URL."""
import os

# Test conversations and tool inputs must not be sent to external tracing services.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
