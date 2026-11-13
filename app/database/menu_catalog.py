"""The names and prices transcribed from assets/menu.png."""

from dataclasses import dataclass

MENU_IMAGE_SHA256 = "f3c4e933883a4c8e9e66886c7c3b98b3c0e5f9849414ec3ed2fce115c5e5cb93"


@dataclass(frozen=True)
class MenuItem:
    name: str
    price: int
    aliases: tuple[str, ...]
    legacy_name: str | None = None
    available: bool | None = None


MENU = (
    MenuItem("كلاسيك برجر", 110, ("classic burger", "برجر كلاسيك", "كلاسيك برغر")),
    MenuItem("تشيز برجر", 125, ("cheese burger", "cheeseburger", "برجر جبنة", "برجر بالجبنة", "برجر تشيز", "cheese purger"), "cheese purger", True),
    MenuItem("دبل برجر", 155, ("double burger", "برجر دبل", "برجر مزدوج")),
    MenuItem("بيتزا مارغريتا", 140, ("margherita pizza", "pizza margherita", "بيتزا مارجريتا", "مارغريتا", "مارجريتا")),
    MenuItem("بيتزا بيبروني", 165, ("pepperoni pizza", "pizza pepperoni", "بيتزا ببروني", "بيبروني")),
    MenuItem("بيتزا خضار", 150, ("vegetable pizza", "veggie pizza", "بيتزا بالخضار")),
    MenuItem("بطاطس مقلية", 40, ("fries", "french fries", "بطاطس", "بطاطس محمرة"), "fries", False),
    MenuItem("حلقات بصل", 45, ("onion rings", "حلقات البصل")),
    MenuItem("قطع دجاج (6 قطع)", 70, ("chicken pieces", "قطع دجاج", "قطع فراخ", "٦ قطع دجاج")),
    MenuItem("سلطة خضراء", 35, ("green salad", "سلطة خضرا")),
    MenuItem("بيبسي", 25, ("pepsi", "بيبسى", "ببسي"), "pepsi", True),
    MenuItem("سفن أب", 25, ("7up", "7 up", "seven up", "سفن", "سفن آب")),
    MenuItem("ميرندا", 25, ("mirinda", "ميراندا")),
    MenuItem("عصير برتقال طبيعي", 45, ("orange juice", "عصير برتقال", "عصير برتقان")),
    MenuItem("مياه معدنية", 15, ("mineral water", "water", "مياه", "مياه معدنيه", "مية")),
    MenuItem("كيك شوكولاتة", 65, ("chocolate cake", "كيك شوكولاته", "كيك شيكولاتة")),
    MenuItem("تشيز كيك", 70, ("cheesecake", "cheese cake", "تشيزكيك")),
)


def ensure_menu_columns(cursor):
    cursor.execute("""
        ALTER TABLE menu_items
          ADD COLUMN IF NOT EXISTS aliases TEXT[] NOT NULL DEFAULT '{}',
          ADD COLUMN IF NOT EXISTS is_listed BOOLEAN NOT NULL DEFAULT TRUE
    """)


def seed_menu(cursor):
    cursor.executemany("""
        INSERT INTO menu_items (name, price, is_available, aliases)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (name) DO NOTHING
    """, [(item.name, item.price, item.available, list(item.aliases)) for item in MENU])


def reconcile_menu(cursor):
    for item in MENU:
        if item.legacy_name:
            # Keep the original ID and any order_items that reference it.
            cursor.execute("""
                UPDATE menu_items SET name = %s
                WHERE name = %s AND NOT EXISTS (SELECT 1 FROM menu_items WHERE name = %s)
            """, (item.name, item.legacy_name, item.name))
        cursor.execute("""
            INSERT INTO menu_items (name, price, is_available, aliases)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
              price = EXCLUDED.price, aliases = EXCLUDED.aliases, is_listed = TRUE
        """, (item.name, item.price, item.available, list(item.aliases)))
    cursor.execute("""
        UPDATE menu_items SET is_listed = FALSE, is_available = FALSE
        WHERE NOT (name = ANY(%s))
    """, ([item.name for item in MENU],))
