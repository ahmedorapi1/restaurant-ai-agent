
import os
import psycopg
from dotenv import load_dotenv
from app.database.menu_catalog import ensure_menu_columns, seed_menu

load_dotenv()
DB_URI = os.environ["DATABASE_URL"]


def init_db_and_seed_data():
    with psycopg.connect(DB_URI) as conn:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS menu_items (
                id SERIAL PRIMARY KEY,
                name varchar(100) NOT NULL UNIQUE,
                price NUMERIC (10,2) NOT NULL CHECK (price>0),
                is_available BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
                """)

            print("menu table created successfully")

            ensure_menu_columns(cursor)
            seed_menu(cursor)

            cursor.execute("""
                SELECT count(*), count(*) FILTER (WHERE is_available IS TRUE)
            FROM menu_items
                WHERE is_listed;
            """)
            listed, available = cursor.fetchone()
            print(f"Menu ready: {listed} listed, {available} confirmed available.")

            cursor.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    customer_name VARCHAR(100) NOT NULL,
                    delivery_phone VARCHAR(20),
                    delivery_address TEXT,
                    status VARCHAR(30) NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending', 'preparing', 'ready',
                            'delivered', 'cancelled'
                        )),
                    total_amount NUMERIC(10, 2) NOT NULL DEFAULT 0
                        CHECK (total_amount >= 0),
                    confirmation_id UUID UNIQUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS order_items (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    order_id UUID NOT NULL
                        REFERENCES orders(id) ON DELETE CASCADE,
                    menu_item_id INTEGER NOT NULL
                        REFERENCES menu_items(id),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    unit_price NUMERIC(10, 2) NOT NULL
                        CHECK (unit_price >= 0),
                    subtotal NUMERIC(10, 2)
                        GENERATED ALWAYS AS (quantity * unit_price) STORED
                );
            """)
        print("Database initialized successfully!")


if __name__ == "__main__":
    init_db_and_seed_data()
