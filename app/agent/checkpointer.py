import os
from dotenv import load_dotenv
from langgraph.checkpoint.postgres import PostgresSaver

load_dotenv()

DB_URL = os.environ["DATABASE_URL"]


def create_checkpointer():
    """Keep this context open for the runner's lifetime.

    from_conn_string configures autocommit=True, dict_row and prepare_threshold=0.
    The runner calls setup() before use and reuses thread_id to restore history.
    """
    return PostgresSaver.from_conn_string(DB_URL)
