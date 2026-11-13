import argparse
from uuid import uuid4

from app.agent.runner import create_agent_runner

def main():
    parser = argparse.ArgumentParser(description="Restaurant AI assistant")
    parser.add_argument("--conversation-id", help="Reuse this id to resume a saved conversation")
    args = parser.parse_args()
    conversation_id = args.conversation_id if args.conversation_id is not None else str(uuid4())
    if not conversation_id.strip():
        parser.error("--conversation-id must not be blank")
    print("Conversation id:", conversation_id)
    with create_agent_runner() as run_agent_turn:
        while True:
            try:
                user_query = input("Client: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_query.lower() == "exit":
                break
            if not user_query:
                continue
            response = run_agent_turn(
                conversation_id,
                user_query
            )
            print("AI:", response)

if __name__ == "__main__":
    main()
