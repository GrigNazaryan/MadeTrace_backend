"""Price estimate and bulk price adjustment (pure Python). Mirrors the prototype's logic.js/business.js."""
import math
from typing import Optional


def js_round(x: float) -> int:
    """JavaScript Math.round (half up) so server and browser agree to the cent."""
    return int(math.floor(x + 0.5))


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float(default)
    return f if math.isfinite(f) else float(default)


def clamp(n, lo, hi) -> float:
    return max(lo, min(hi, _num(n)))


def find_catalog_product(line: dict, products: list) -> Optional[dict]:
    for p in products:
        if p.get("id") == line.get("productId"):
            return p
    if not line.get("productId"):
        for p in products:
            if p.get("name") == line.get("name"):
                return p
    return None


def calculate_print_estimate(lines: list, pricing: dict, products: list) -> dict:
    breakdown = []
    subtotal = 0.0
    for line in lines:
        quantity = max(1, js_round(_num(line.get("quantity"), 1)))
        product = find_catalog_product(line, products)
        if product:
            blank = _num(product.get("base")) + _num((product.get("sizeAdjustments") or {}).get(line.get("size"), 0))
        else:
            blank = _num(line.get("blankPrice", pricing.get("blank", 0)))
        blank = max(0.0, blank)
        default_rate = max(0.0, _num(pricing.get("print")))
        sides = line.get("placements") or line.get("sides") or []
        placements = max(1, len(sides) or 1)
        side_list = sides or ["front"]
        rates = []
        for side in side_list:
            width = _num((line.get("printWidthsCm") or {}).get(side), 0)
            if width > 21:
                rates.append(max(0.0, _num(pricing.get("printLarge", default_rate), default_rate)))
            elif 0 < width <= 12:
                rates.append(max(0.0, _num(pricing.get("printSmall", default_rate), default_rate)))
            else:
                rates.append(default_rate)
        print_per_unit = sum(rates)
        setup = max(0.0, _num(pricing.get("setup")))
        base = quantity * (blank + print_per_unit)
        threshold = max(2, _num(pricing.get("discountAt"), 25) or 25)
        discount = clamp(pricing.get("discountPct"), 0, 100) / 100 if quantity >= threshold else 0.0
        line_amount = js_round((base * (1 - discount) + setup) * 100) / 100
        subtotal += line_amount
        breakdown.append({
            "name": line.get("name") or "Custom garment", "quantity": quantity, "placements": placements,
            "blank": blank, "printPerUnit": print_per_unit, "setup": setup, "discount": discount, "total": line_amount,
        })
    return {"total": js_round(subtotal * 100) / 100, "breakdown": breakdown}


def preview_price_adjustment(products: list, scope="all", selected=None, category="", percent=0.0, rounding=0.01) -> list:
    """Raises ValueError(code). Returns [{id,name,before,after,cost}]."""
    selected = selected or []
    percent = _num(percent, float("nan"))
    if not math.isfinite(percent) or percent < -100 or percent > 1000:
        raise ValueError("bad_percent")
    if rounding not in (0.01, 0.1, 1, 1.0):
        raise ValueError("bad_rounding")
    rows = []
    for p in products:
        in_scope = scope == "all" or (scope == "selected" and p["id"] in selected) or (scope == "category" and p.get("category") == category)
        if not in_scope:
            continue
        before = _num(p.get("base"))
        after = js_round(js_round(before * (1 + percent / 100) / rounding) * rounding * 100) / 100
        if abs(after) > 10_000_000:
            raise ValueError("price_out_of_range")
        rows.append({"id": p["id"], "name": p.get("name"), "before": before, "after": after, "cost": p.get("acquisitionCost")})
    if not rows:
        raise ValueError("empty_scope")
    return rows
