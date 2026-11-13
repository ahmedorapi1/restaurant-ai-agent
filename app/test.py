from dotenv import load_dotenv
import psycopg
import os

load_dotenv()

DB_URL = os.environ["DATABASE_URL"]

try:
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM orders WHERE status = 'pending';
            """)
            print(cursor.fetchall())
except Exception as e:
    print(f"Database Error: {e}")
