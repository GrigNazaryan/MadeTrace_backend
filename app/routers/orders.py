"""Orders — customer side and the shared read/message endpoints."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, serializers
from ..database import get_db
from ..domain import order_state
from ..domain import validation as v
from ..domain.pricing import calculate_print_estimate
from ..orders_common import (add_event, add_message, bad, create_order_with_retry, latest_proof, latest_quote,
                            load_order, proof_dict, quote_dict, recompute_status)
from ..ratelimit import check
from ..security import get_current_user, require_customer
from ..subscriptions import is_live

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _product_dict(p: models.Product) -> dict:
    return serializers.product_out(p, public=False)


def _clean_attachments(items) -> list:
    out = []
    for a in items or []:
        try:
            url = v.storage_url(a.get("url"), "attachment", required=True)
            out.append({"id": v.text(a.get("id") or uuid.uuid4().hex[:12], 64),
                        "name": v.text(a.get("name"), 120, True, "attachment"),
                        "type": v.text(a.get("type"), 80), "size": int(v.number(a.get("size"), 0, 100_000_000, "attachment", 0)),
                        "url": url})
        except ValueError as err:
            raise bad(str(err), 422)
    return out


@router.post("", status_code=201)
def create_order(body: schemas.OrderCreateIn, user: models.User = Depends(require_customer), db: Session = Depends(get_db)):
    if user.impersonated_by:
        raise HTTPException(status_code=403, detail="impersonation_read_only")
    check(f"order:{user.id}", 20, 3600)

    workshop = db.query(models.Workshop).filter(models.Workshop.id == body.workshopId).first()
    if workshop is None or not workshop.is_public or not is_live(workshop):
        raise bad("workshop_unavailable", 404)

    catalog = {p.id: _product_dict(p) for p in workshop.products if p.published}
    lines = []
    try:
        for raw in body.lines:
            lines.append(v.order_line(raw, catalog.get(raw.get("productId") if isinstance(raw, dict) else None)))
    except ValueError as err:
        raise bad(str(err), 422)
    if len({line["id"] for line in lines}) != len(lines):
        raise bad("invalid:line", 422)

    pricing = workshop.pricing or {}
    estimate = None
    if pricing.get("mode") == "auto":
        estimate = calculate_print_estimate(lines, pricing, list(catalog.values()))["total"]

    total_units = sum(line["quantity"] for line in lines)
    title = (lines[0]["name"] if len(lines) == 1 else f"{len(lines)} items") + f" · {total_units}"
    note = v.text(body.note, 4000)
    attachments = _clean_attachments(body.attachments)
    name = v.text(body.customerName, 120) or user.full_name or user.email

    def build(number: str) -> models.Order:
        return models.Order(number=number, workshop_id=workshop.id, customer_id=user.id, source="customer",
                            customer_name=name, customer_email=user.email, title=title, note=note,
                            currency=workshop.currency, status="request", lines=lines, estimate=estimate,
                            attachments=attachments, schedule={}, last_read={})

    order = create_order_with_retry(db, build)
    add_event(db, order, "request_submitted")
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "customer")


@router.get("/mine")
def my_orders(user: models.User = Depends(require_customer), db: Session = Depends(get_db)):
    rows = (db.query(models.Order).filter(models.Order.customer_id == user.id)
            .order_by(models.Order.created_at.desc()).limit(300).all())
    return [serializers.order_out(o, "customer", light=True) for o in rows]


@router.get("/{number}")
def get_order(number: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    order, viewer = load_order(db, user, number)
    return serializers.order_out(order, viewer)


@router.post("/{number}/read")
def mark_read(number: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    order, viewer = load_order(db, user, number)
    if user.impersonated_by or user.role == models.UserRole.ADMIN:
        return {"ok": True}   # looking around must not change read markers
    if order.messages:
        last = dict(order.last_read or {})
        last[viewer] = order.messages[-1].id
        order.last_read = last
        db.commit()
    return {"ok": True}


@router.post("/{number}/messages", status_code=201)
def send_message(number: str, body: schemas.MessageIn, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    order, viewer = load_order(db, user, number)
    if user.role == models.UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="forbidden")
    check(f"msg:{user.id}", 60, 600)
    text_value = v.text(body.text, 4000, True, "message")
    add_message(db, order, "staff" if viewer == "staff" else "customer", text_value)
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, viewer)


@router.post("/{number}/quotes/{version}/decision")
def decide_quote(number: str, version: int, body: schemas.DecisionIn, user: models.User = Depends(require_customer),
                 db: Session = Depends(get_db)):
    if user.impersonated_by:
        raise HTTPException(status_code=403, detail="impersonation_read_only")
    order, _ = load_order(db, user, number, require="customer")
    quote = latest_quote(order)
    if order.status == "cancelled":
        raise bad("order_cancelled")
    if quote is None or quote.version != version:
        raise bad("quote_outdated", 409)
    if quote.decision:
        raise bad("quote_already_decided", 409)
    quote.decision = body.decision
    quote.decided_at = datetime.now(timezone.utc)
    add_event(db, order, "quote_decision", {"decision": body.decision, "version": quote.version})
    if body.decision == "accepted":
        order.status = recompute_status(order)
    elif body.decision == "revision":
        order.status = "clarification"
    else:
        order.resume_status = order.status
        order.status = "cancelled"
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "customer")


@router.post("/{number}/proofs/{version}/approve")
def approve_proof(number: str, version: int, user: models.User = Depends(require_customer), db: Session = Depends(get_db)):
    if user.impersonated_by:
        raise HTTPException(status_code=403, detail="impersonation_read_only")
    order, _ = load_order(db, user, number, require="customer")
    proof = latest_proof(order)
    if order.status == "cancelled":
        raise bad("order_cancelled")
    if proof is None or proof.version != version:
        raise bad("proof_outdated", 409)
    if proof.approved:
        raise bad("proof_already_approved", 409)
    if proof.revision_requested:
        raise bad("proof_revision_pending", 409)
    proof.approved = True
    proof.approved_at = datetime.now(timezone.utc)
    add_event(db, order, "proof_approved", {"version": proof.version})
    order.status = recompute_status(order)
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "customer")


@router.post("/{number}/proofs/{version}/revise")
def revise_proof(number: str, version: int, user: models.User = Depends(require_customer), db: Session = Depends(get_db)):
    if user.impersonated_by:
        raise HTTPException(status_code=403, detail="impersonation_read_only")
    order, _ = load_order(db, user, number, require="customer")
    proof = latest_proof(order)
    if order.status == "cancelled":
        raise bad("order_cancelled")
    if proof is None or proof.version != version:
        raise bad("proof_outdated", 409)
    if proof.approved or proof.revision_requested:
        raise bad("proof_already_decided", 409)
    proof.revision_requested = True
    add_event(db, order, "proof_revision", {"version": proof.version})
    add_message(db, order, "customer", "", code="proof_revision_request", params={"version": proof.version})
    order.status = recompute_status(order)
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "customer")


@router.post("/{number}/repeat", status_code=201)
def repeat_order(number: str, user: models.User = Depends(require_customer), db: Session = Depends(get_db)):
    """Copies a past order into a fresh request (artwork re-validated against today's catalog)."""
    if user.impersonated_by:
        raise HTTPException(status_code=403, detail="impersonation_read_only")
    check(f"order:{user.id}", 20, 3600)
    old, _ = load_order(db, user, number, require="customer")
    workshop = old.workshop
    if not workshop.is_public or not is_live(workshop):
        raise bad("workshop_unavailable", 404)
    catalog = {p.id: _product_dict(p) for p in workshop.products if p.published}
    lines = []
    try:
        for raw in old.lines or []:
            fresh = dict(raw)
            fresh["id"] = "line-" + uuid.uuid4().hex[:10]
            lines.append(v.order_line(fresh, catalog.get(raw.get("productId"))))
    except ValueError:
        raise bad("repeat_unavailable", 409)
    if not lines:
        raise bad("repeat_unavailable", 409)
    pricing = workshop.pricing or {}
    estimate = calculate_print_estimate(lines, pricing, list(catalog.values()))["total"] if pricing.get("mode") == "auto" else None

    def build(num: str) -> models.Order:
        return models.Order(number=num, workshop_id=workshop.id, customer_id=user.id, source="customer",
                            customer_name=old.customer_name, customer_email=user.email, title=old.title,
                            note="", currency=workshop.currency, status="request", lines=lines, estimate=estimate,
                            attachments=[], schedule={}, last_read={})

    order = create_order_with_retry(db, build)
    add_event(db, order, "order_repeated", {"from": old.number})
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "customer")
