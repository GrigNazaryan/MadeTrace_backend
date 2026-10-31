"""Calendar-date helpers and production schedule validation (pure Python)."""
import re
from datetime import date

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_KEYS = ("productionStart", "productionDue", "estimatedDate", "promisedDate")


def valid_date_only(value) -> bool:
    if not isinstance(value, str) or not _DATE.match(value):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_schedule(previous: dict, nxt: dict, comment: str = "") -> dict:
    """Raises ValueError(code). Returns which parts changed."""
    previous = previous or {}
    for key in DATE_KEYS:
        if nxt.get(key) and not valid_date_only(nxt[key]):
            raise ValueError("invalid_date")
    start, due = nxt.get("productionStart"), nxt.get("productionDue")
    if start and due and start > due:
        raise ValueError("deadline_before_start")
    promise_changed = (previous.get("promisedDate") or "") != (nxt.get("promisedDate") or "")
    if previous.get("promisedDate") and promise_changed and not (comment or "").strip():
        raise ValueError("promise_change_needs_comment")
    internal_changed = any((previous.get(k) or "") != (nxt.get(k) or "")
                           for k in ("productionStart", "productionDue", "estimatedDate"))
    return {"internalChanged": internal_changed, "promiseChanged": promise_changed}
