"""Input sanitising for everything the browser sends us (pure Python).

All functions raise ValueError(code) on bad input and return a cleaned copy otherwise.
Nothing the browser sends is trusted: layer geometry, URLs, colours, sizes, text are all checked here.
"""
import math
import re
from typing import Any, Optional

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
STORAGE_MARKER = "/storage/v1/object/public/"
MOCKUP_TYPES = ("tee", "hoodie", "tank", "tote", "reference")
SIDES = ("front", "back")
FONTS = {
    "Arial", "Segoe UI", "Verdana", "Tahoma", "Trebuchet MS", "Calibri", "Candara", "Century Gothic",
    "Bahnschrift", "Impact", "Georgia", "Times New Roman", "Cambria", "Constantia",
    "Palatino Linotype", "Courier New", "Consolas",
}
MAX_LAYERS_PER_SIDE = 40
MAX_LINES = 30


def text(value: Any, max_len: int, required: bool = False, field: str = "text") -> str:
    s = "" if value is None else str(value).strip()
    if required and not s:
        raise ValueError(f"required:{field}")
    return s[:max_len]


def number(value: Any, lo: float, hi: float, field: str = "number", default: Optional[float] = None) -> float:
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"invalid:{field}")
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid:{field}")
    if not math.isfinite(f) or f < lo or f > hi:
        raise ValueError(f"invalid:{field}")
    return f


def hex_color(value: Any, field: str = "color") -> str:
    if not isinstance(value, str) or not HEX.match(value):
        raise ValueError(f"invalid:{field}")
    return value.lower()


def http_url(value: Any, field: str = "url", required: bool = False) -> Optional[str]:
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError(f"required:{field}")
        return None
    v = str(value).strip()
    if len(v) > 600 or not (v.startswith("https://") or v.startswith("http://")) or " " in v:
        raise ValueError(f"invalid:{field}")
    return v


def storage_url(value: Any, field: str = "file", required: bool = False) -> Optional[str]:
    """Only files uploaded to our public storage bucket (blocks javascript:, data: and foreign hosts)."""
    v = http_url(value, field, required)
    if v is None:
        return None
    if not v.startswith("https://") or STORAGE_MARKER not in v:
        raise ValueError(f"invalid:{field}")
    return v


def email(value: Any, field: str = "email") -> str:
    v = text(value, 254, True, field).lower()
    if not EMAIL.match(v):
        raise ValueError(f"invalid:{field}")
    return v


def layer(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("invalid:layer")
    kind = raw.get("type")
    if kind not in ("text", "image"):
        raise ValueError("invalid:layer")
    out = {
        "id": text(raw.get("id"), 64, True, "layer_id"),
        "type": kind,
        "x": number(raw.get("x"), -200, 900, "layer"),
        "y": number(raw.get("y"), -200, 900, "layer"),
        "width": number(raw.get("width"), 1, 2000, "layer", 80),
        "height": number(raw.get("height"), 1, 2000, "layer", 30),
        "rotation": number(raw.get("rotation"), -360, 360, "layer", 0),
        "visible": raw.get("visible") is not False,
    }
    if raw.get("opacity") is not None:
        out["opacity"] = number(raw.get("opacity"), 0, 1, "layer", 1)
    if kind == "text":
        out["text"] = text(raw.get("text"), 80, False, "layer_text") or "TEXT"
        out["font"] = raw.get("font") if raw.get("font") in FONTS else "Arial"
        out["fontSize"] = number(raw.get("fontSize"), 8, 200, "layer", 30)
        out["color"] = hex_color(raw.get("color") or "#234d78", "layer_color")
    else:
        out["src"] = storage_url(raw.get("src"), "layer_image", required=True)
        for key in ("originalWidth", "originalHeight"):
            if raw.get(key) is not None:
                out[key] = int(number(raw.get(key), 1, 20000, "layer", 1))
    return out


def design(raw: Any) -> dict:
    if raw is None:
        return {"front": [], "back": []}
    if not isinstance(raw, dict):
        raise ValueError("invalid:design")
    out = {}
    for side in SIDES:
        items = raw.get(side) or []
        if not isinstance(items, list) or len(items) > MAX_LAYERS_PER_SIDE:
            raise ValueError("invalid:design")
        out[side] = [layer(item) for item in items]
    return out


def order_line(raw: Any, product: Optional[dict]) -> dict:
    """One request line. `product` is the workshop's catalog entry (dict) — required for customer orders."""
    if not isinstance(raw, dict):
        raise ValueError("invalid:line")
    if product is None:
        raise ValueError("unknown_product")
    colors = product.get("colors") or []
    sizes = product.get("sizes") or []
    color = raw.get("color") or (colors[0] if colors else "#f7f7f5")
    size = raw.get("size") or (sizes[0] if sizes else "")
    if colors and color not in colors:
        raise ValueError("invalid:color")
    if sizes and size not in sizes:
        raise ValueError("invalid:size")
    quantity = int(number(raw.get("quantity"), 1, 10000, "quantity", 1))
    des = design(raw.get("design"))
    placements = [s for s in SIDES if des[s]] or ["front"]
    widths = {}
    for side in placements:
        raw_w = (raw.get("printWidthsCm") or {}).get(side)
        widths[side] = number(raw_w, 0, 200, "print_width", 0)
    return {
        "id": text(raw.get("id"), 64, True, "line_id"),
        "productId": product["id"],
        "mockupType": product.get("mockupType") or "tee",
        "name": product["name"],
        "color": color,
        "size": size,
        "quantity": quantity,
        "design": des,
        "placements": placements,
        "printWidthsCm": widths,
        "method": text(raw.get("method"), 80, False, "method"),
        "image": product.get("image"),
        "printAreas": product.get("printAreas") or {},
        "blankPrice": float(product.get("base") or 0) + float((product.get("sizeAdjustments") or {}).get(size, 0) or 0),
    }


def csv_import_quantity(value: Any) -> bool:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return False
    return n == int(n) and 1 <= n <= 10000


def csv_cell(value: Any) -> str:
    """Neutralise spreadsheet formula injection in exported/imported CSV text."""
    s = "" if value is None else str(value)
    return ("'" + s) if s[:1] in ("=", "+", "-", "@") else s
