import os
from pathlib import Path
from urllib.parse import urlsplit

from langchain_core.tools import tool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_MENU_URL = "/api/menu/image"
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif"}


def local_menu_file() -> Path | None:
    configured = os.getenv("MENU_IMAGE_PATH", "assets/menu.png").strip()
    if not configured:
        return None
    path = PROJECT_ROOT / configured
    return path if path.suffix.lower() in IMAGE_TYPES and path.is_file() else None


def valid_menu_url(url: str) -> bool:
    if url == LOCAL_MENU_URL:
        return True
    try:
        parsed = urlsplit(url)
        return bool(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password)
    except ValueError:
        return False


@tool
def get_menu_image() -> dict:
    """Show the static restaurant menu image when the customer asks to see it.

    Menu items, availability, and prices for ordering must come from the menu database.
    """
    remote_url = os.getenv("MENU_IMAGE_URL", "").strip()
    if remote_url and remote_url != LOCAL_MENU_URL and valid_menu_url(remote_url):
        return {"success": True, "type": "image", "url": remote_url}
    if local_menu_file():
        return {"success": True, "type": "image", "url": LOCAL_MENU_URL}
    return {"success": False, "error": "صورة المنيو مش متاحة حاليًا. أقدر أقول لحضرتك الأصناف المتاحة."}
