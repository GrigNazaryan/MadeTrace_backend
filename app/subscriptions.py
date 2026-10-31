"""Single source of truth for subscription state and what it unlocks."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, or_

from . import models


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_live(workshop: models.Workshop, now: Optional[datetime] = None) -> bool:
    """True while the workshop may receive new requests and appear in the directory."""
    now = now or _now()
    if workshop.is_blocked or workshop.subscription_status not in ("trial", "active"):
        return False
    exp = workshop.subscription_expires_at
    return exp is None or exp > now


def effective_plan(workshop: models.Workshop) -> str:
    """The plan whose features are available right now (Growth features stop when the subscription lapses)."""
    return workshop.plan if is_live(workshop) else "core"


def has_growth(workshop: models.Workshop) -> bool:
    return is_live(workshop) and workshop.plan == "growth"


def sync_subscription(workshop: models.Workshop) -> bool:
    """Marks the subscription EXPIRED once its period is over. Returns True when the row changed."""
    exp = workshop.subscription_expires_at
    if exp is not None and workshop.subscription_status in ("trial", "active") and exp <= _now():
        workshop.subscription_status = "expired"
        return True
    return False


def live_clause():
    """SQL condition equivalent to is_live()."""
    now = _now()
    return and_(
        models.Workshop.is_blocked.is_(False),
        models.Workshop.subscription_status.in_(["trial", "active"]),
        or_(models.Workshop.subscription_expires_at.is_(None), models.Workshop.subscription_expires_at > now),
    )
