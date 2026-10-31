"""Shared helpers for everything that touches an order."""
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from .domain import order_state


def next_number(db: Session) -> str:
    row = db.execute(text(
        "SELECT COALESCE(MAX(CAST(SUBSTRING(number FROM 4) AS INTEGER)), 1000) FROM mt_orders"
    )).scalar()
    return f"MT-{int(row) + 1}"


def load_order(db: Session, user: models.User, number: str, require: Optional[str] = None) -> Tuple[models.Order, str]:
    """Finds an order and works out how this user may see it.

    Returns (order, viewer) where viewer is 'staff' (the workshop's owner or an admin) or 'customer'.
    require='staff' / 'customer' rejects the other side. Unknown AND foreign orders both answer 404,
    so nobody can probe which order numbers exist.
    """
    order = db.query(models.Order).filter(models.Order.number == number).first()
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    viewer = None
    if user.role == models.UserRole.ADMIN:
        viewer = "staff"
    elif user.role == models.UserRole.COMPANY:
        ws = db.query(models.Workshop).filter(models.Workshop.owner_id == user.id).first()
        if ws is not None and ws.id == order.workshop_id:
            viewer = "staff"
    elif user.role == models.UserRole.CUSTOMER and order.customer_id == user.id:
        viewer = "customer"
    if viewer is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    if require and viewer != require:
        raise HTTPException(status_code=403, detail="forbidden")
    return order, viewer


def add_event(db: Session, order: models.Order, code: str, params: Optional[dict] = None, visibility: str = "public"):
    db.add(models.OrderEvent(order_id=order.id, code=code, params=params or {}, visibility=visibility))


def add_message(db: Session, order: models.Order, sender: str, text_value: str = "", code: Optional[str] = None,
                params: Optional[dict] = None):
    msg = models.OrderMessage(order_id=order.id, sender=sender, text=text_value, code=code, params=params or {})
    db.add(msg)
    return msg


def latest_quote(order: models.Order) -> Optional[models.OrderQuote]:
    return order.quotes[-1] if order.quotes else None


def latest_proof(order: models.Order) -> Optional[models.OrderProof]:
    return order.proofs[-1] if order.proofs else None


def quote_dict(order: models.Order) -> Optional[dict]:
    q = latest_quote(order)
    return {"decision": q.decision, "version": q.version} if q else None


def proof_dict(order: models.Order) -> Optional[dict]:
    p = latest_proof(order)
    return {"version": p.version, "approved": bool(p.approved), "revisionRequested": bool(p.revision_requested)} if p else None


def recompute_status(order: models.Order) -> str:
    return order_state.approval_status(order.status, quote_dict(order), proof_dict(order), bool(order.quotes))


def create_order_with_retry(db: Session, build) -> models.Order:
    """`build(number)` returns a new Order. Retries when two requests pick the same number."""
    for _ in range(6):
        order = build(next_number(db))
        db.add(order)
        try:
            db.flush()
            return order
        except IntegrityError:
            db.rollback()
    raise HTTPException(status_code=500, detail="could_not_create_order")


def bad(code: str, status_code: int = 400):
    return HTTPException(status_code=status_code, detail=code)
