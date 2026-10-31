"""Tiny helper that writes rows into the audit log (admin impersonation etc.)."""
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from . import models


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None


def record_audit(
    db: Session,
    *,
    admin_id: Optional[str],
    action: str,
    request: Optional[Request] = None,
    target_workshop_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    db.add(models.AuditLog(
        admin_id=admin_id,
        action=action,
        target_workshop_id=target_workshop_id,
        target_user_id=target_user_id,
        method=request.method if request is not None else None,
        path=request.url.path[:300] if request is not None else None,
        ip=_client_ip(request),
        detail=detail,
    ))
    db.commit()
