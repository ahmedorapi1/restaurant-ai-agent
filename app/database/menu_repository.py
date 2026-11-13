import os
import re
import unicodedata
from rapidfuzz import fuzz
from psycopg.rows import dict_row
import psycopg
from dotenv import load_dotenv


load_dotenv()
DB_URI = os.environ["DATABASE_URL"]

_ARABIC_VARIANTS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه",
    "چ": "ج", "ک": "ك", "ی": "ي", "ـ": "",
})
_CATEGORY_SPELLINGS = {"برغر": "برجر", "البرجر": "برجر", "البرغر": "برجر", "البيتزا": "بيتزا"}


def normalize_item_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).casefold().translate(_ARABIC_VARIANTS)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(_CATEGORY_SPELLINGS.get(word, word) for word in re.findall(r"[^\W_]+", text))


def match_menu_items(items: list[dict], name: str) -> list[dict]:
    query = normalize_item_name(name)
    if not query:
        return []
    return [
        item for item in items
        if query in {normalize_item_name(label) for label in [item["name"], *item.get("aliases", [])]}
    ]


def normalize_menu_query(query: str) -> str:
    text = normalize_item_name(query)
    prefixes = (
        "لو سمحت ", "من فضلك ", "عايز ", "عاوز ", "عايزه ", "عاوزه ",
        "محتاج ", "حابب ", "اريد ", "هات ", "ضيف ", "زود ", "عندكم ", "فيه ", "ممكن ",
        "i want ", "i d like ",
    )
    previous = None
    while text != previous:
        previous = text
        for prefix in prefixes:
            text = text.removeprefix(prefix)
        text = text.removesuffix(" لو سمحت").removesuffix(" من فضلك")
    return text


def _fuzzy_score(query_words: list[str], label_words: list[str]) -> float:
    # A misspelled category may match a word. Product modifiers must all survive.
    if len(query_words) == 1:
        word = query_words[0]
        if len(word) < 4:
            return 0
        return max((fuzz.ratio(word, candidate) for candidate in label_words if len(candidate) >= 4), default=0)
    if len(query_words) != len(label_words) or any(len(word) < 3 for word in query_words):
        return 0
    pairs = list(zip(sorted(query_words), sorted(label_words)))
    if any(fuzz.ratio(left, right) < 75 for left, right in pairs):
        return 0
    return fuzz.token_sort_ratio(" ".join(query_words), " ".join(label_words))


def search_menu_items(items: list[dict], name: str) -> dict:
    """Search listed products. Only a unique exact name/verified alias resolves an ID."""
    query = normalize_menu_query(name)
    listed = [item for item in items if item.get("is_listed", True)]
    exact = match_menu_items(listed, query) if query else []
    quantity = None
    count, _, product = query.partition(" ")
    # Try the exact name first so a numeric name such as "7 up" stays intact.
    if not exact and product and count.isdecimal() and len(count) <= 10 and 0 < int(count) <= 2_147_483_647:
        quantity = int(count)
        query = product
        exact = match_menu_items(listed, query)
    scores = {item["id"]: 100.0 for item in exact}
    match_type = "none"
    if exact:
        match_type = "exact" if any(normalize_item_name(item["name"]) == query for item in exact) else "alias"
    elif query:
        words = query.split()
        query_tokens = set(words)
        labels = {
            item["id"]: [normalize_item_name(label).split() for label in [item["name"], *item.get("aliases", [])]]
            for item in listed
        }
        scores = {
            item_id: 100.0 for item_id, variants in labels.items()
            if any(query_tokens.issubset(label) for label in variants)
        }
        if scores:
            match_type = "category" if len(words) == 1 else "keyword"
        else:
            # Prefixes must cover every requested word, including modifiers.
            scores = {
                item_id: 90.0 for item_id, variants in labels.items()
                if all(len(word) >= 3 for word in words) and any(
                    all(any(candidate.startswith(word) for candidate in label) for word in words)
                    for label in variants
                )
            }
            if scores:
                match_type = "partial"
            else:
                scores = {
                    item_id: max((_fuzzy_score(words, label) for label in variants), default=0)
                    for item_id, variants in labels.items()
                }
                best = max(scores.values(), default=0)
                scores = {item_id: score for item_id, score in scores.items() if score >= 88 and score >= best - 5}
                if scores:
                    match_type = "fuzzy"
    matches = sorted(
        (item for item in listed if item["id"] in scores),
        key=lambda item: (-scores[item["id"]], item["is_available"] is not True, item["id"]),
    )
    results = [{
        "id": item["id"], "menu_item_id": item["id"], "name": item["name"],
        "price": str(item["price"]), "is_available": item["is_available"],
        "score": round(scores[item["id"]], 2),
    } for item in matches]
    metadata = {"query": query, "quantity": quantity, "match_type": match_type}
    if len(exact) == 1:
        return {**metadata, "status": "matched", "item": results[0], "suggestions": []}
    return {
        "status": "needs_confirmation" if results else "not_found",
        **metadata, "suggestions": results,
    }


def read_menu(conn, ids: list[int] | None = None, *, lock: bool = False):
    query = "SELECT id, name, price, is_available, aliases FROM menu_items WHERE is_listed"
    params = ()
    if ids is not None:
        query += " AND id = ANY(%s)"
        params = (ids,)
    query += " ORDER BY id"
    if lock:
        query += " FOR SHARE"
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(query, params).fetchall()


def get_all_menu_items():
    with psycopg.connect(DB_URI) as conn:
        return read_menu(conn)

