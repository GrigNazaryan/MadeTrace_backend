"""The workshop ("company") console: profile & settings, product catalog, pricing rules,
bulk repricing (Growth), design templates, and the staff side of order handling
(quotes, proofs, status, schedule, notes, CSV import, clients, reports).

Everything here sits behind `get_current_workshop`, so it is reachable both by the
workshop's own owner and, read-only unless an admin explicitly opened a write
session, by an admin who is "logged in as" that company (see security.py).
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import models, schemas, serializers
from ..database import get_db
from ..domain import order_state
from ..domain import validation as v
from ..domain.pricing import js_round, preview_price_adjustment
from ..domain.schedule import validate_schedule, valid_date_only
from ..orders_common import (add_event, add_message, bad, create_order_with_retry, load_order,
                            proof_dict, quote_dict, recompute_status)
from ..ratelimit import check
from ..security import get_current_user, get_current_workshop
from ..subscriptions import has_growth

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


# =============================================================================
# Profile, preferences, pricing rules
# =============================================================================

@router.get("/profile")
def get_profile(workshop: models.Workshop = Depends(get_current_workshop)):
    return serializers.workshop_private(workshop)


@router.put("/profile")
def update_profile(body: schemas.ProfileIn, workshop: models.Workshop = Depends(get_current_workshop),
                   db: Session = Depends(get_db)):
    try:
        pay_link = v.http_url(body.paymentLink, "payment_link") if body.paymentLink else None
        contact = {k: v.text(val, 160, field="contact") for k, val in (body.publicContact or {}).items()
                  if k in ("email", "phone", "website")}
    except ValueError as err:
        raise bad(str(err), 422)

    workshop.name = v.text(body.name, 120, True, "name")
    workshop.country = body.country.upper()
    workshop.city = v.text(body.city, 80, True, "city")
    workshop.service_area = v.text(body.serviceArea, 160)
    workshop.services = [v.text(s, 60, field="service") for s in body.services if s.strip()][:12]
    workshop.description = v.text(body.description, 400)
    workshop.about = v.text(body.about, 6000)
    workshop.pickup = body.pickup
    workshop.is_public = body.public
    workshop.public_contact = contact
    workshop.payment_link = pay_link
    db.commit()
    db.refresh(workshop)
    return serializers.workshop_private(workshop)


@router.put("/preferences")
def update_preferences(body: schemas.PreferencesIn, workshop: models.Workshop = Depends(get_current_workshop),
                       db: Session = Depends(get_db)):
    workshop.timezone = v.text(body.timezone, 60, True, "timezone")
    workshop.currency = body.currency.upper()
    db.commit()
    db.refresh(workshop)
    return serializers.workshop_private(workshop)


@router.put("/pricing")
def update_pricing(body: schemas.PricingIn, workshop: models.Workshop = Depends(get_current_workshop),
                   db: Session = Depends(get_db)):
    """Stores the estimate rules as-is (merged over whatever keys the frontend doesn't send,
    e.g. the legacy `blank` fallback used by the price calculator)."""
    workshop.pricing = {**(workshop.pricing or {}), **body.model_dump()}
    db.commit()
    db.refresh(workshop)
    return serializers.workshop_private(workshop)


# =============================================================================
# Product catalog
# =============================================================================

def _clean_product_fields(body: schemas.ProductIn) -> dict:
    colors = [v.hex_color(c, "color") for c in body.colors]
    sizes = [v.text(s, 12, True, "size") for s in body.sizes if s.strip()]
    image = v.storage_url(body.image, "product_image") if body.image else None
    return {
        "name": v.text(body.name, 120, True, "name"),
        "description": v.text(body.description, 1000),
        "category": v.text(body.category, 60) or "Apparel",
        "base": body.base,
        "acquisition_cost": body.acquisitionCost,
        "published": body.published,
        "colors": colors,
        "sizes": sizes,
        "size_adjustments": {k: float(val) for k, val in (body.sizeAdjustments or {}).items()},
        "mockup_type": body.mockupType,
        "print_areas": body.printAreas or {},
        "image_url": image,
    }


@router.get("/products")
def list_products(workshop: models.Workshop = Depends(get_current_workshop)):
    return [serializers.product_out(p) for p in workshop.products]


@router.post("/products", status_code=201)
def create_product(body: schemas.ProductIn, workshop: models.Workshop = Depends(get_current_workshop),
                   db: Session = Depends(get_db)):
    try:
        fields = _clean_product_fields(body)
    except ValueError as err:
        raise bad(str(err), 422)
    next_order = db.query(func.coalesce(func.max(models.Product.sort_order), -1)) \
        .filter(models.Product.workshop_id == workshop.id).scalar()
    product = models.Product(workshop_id=workshop.id, sort_order=int(next_order) + 1, **fields)
    db.add(product)
    db.commit()
    db.refresh(product)
    return serializers.product_out(product)


@router.put("/products/{product_id}")
def update_product(product_id: str, body: schemas.ProductIn, workshop: models.Workshop = Depends(get_current_workshop),
                   db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.id == product_id,
                                               models.Product.workshop_id == workshop.id).first()
    if product is None:
        raise bad("product_not_found", 404)
    try:
        fields = _clean_product_fields(body)
    except ValueError as err:
        raise bad(str(err), 422)
    for key, value in fields.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return serializers.product_out(product)


@router.delete("/products/{product_id}")
def delete_product(product_id: str, workshop: models.Workshop = Depends(get_current_workshop),
                   db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.id == product_id,
                                               models.Product.workshop_id == workshop.id).first()
    if product is None:
        raise bad("product_not_found", 404)
    db.delete(product)
    db.commit()
    return {"ok": True}


# --- Bulk repricing (Growth only) -------------------------------------------

@router.post("/products/bulk-price/preview")
def bulk_price_preview(body: schemas.BulkPreviewIn, workshop: models.Workshop = Depends(get_current_workshop)):
    if not has_growth(workshop):
        raise bad("growth_required", 403)
    catalog = [serializers.product_out(p) for p in workshop.products]
    try:
        rows = preview_price_adjustment(catalog, scope=body.scope, selected=body.selected, category=body.category,
                                        percent=body.percent, rounding=body.rounding)
    except ValueError as err:
        raise bad(str(err), 422)
    return {"rows": rows}


@router.post("/products/bulk-price/apply", status_code=201)
def bulk_price_apply(body: schemas.BulkPreviewIn, workshop: models.Workshop = Depends(get_current_workshop),
                     db: Session = Depends(get_db)):
    if not has_growth(workshop):
        raise bad("growth_required", 403)
    check(f"bulk_price:{workshop.id}", 10, 3600)
    catalog = [serializers.product_out(p) for p in workshop.products]
    try:
        rows = preview_price_adjustment(catalog, scope=body.scope, selected=body.selected, category=body.category,
                                        percent=body.percent, rounding=body.rounding)
    except ValueError as err:
        raise bad(str(err), 422)
    by_id = {p.id: p for p in workshop.products}
    for row in rows:
        by_id[row["id"]].base = row["after"]
    adjustment = models.PriceAdjustment(workshop_id=workshop.id, percent=body.percent, currency=workshop.currency,
                                        rows=rows)
    db.add(adjustment)
    db.commit()
    db.refresh(adjustment)
    return {"id": adjustment.id, "rows": rows, "createdAt": serializers.iso(adjustment.created_at)}


@router.post("/products/bulk-price/{adjustment_id}/undo")
def bulk_price_undo(adjustment_id: str, workshop: models.Workshop = Depends(get_current_workshop),
                    db: Session = Depends(get_db)):
    adjustment = db.query(models.PriceAdjustment).filter(
        models.PriceAdjustment.id == adjustment_id, models.PriceAdjustment.workshop_id == workshop.id).first()
    if adjustment is None:
        raise bad("adjustment_not_found", 404)
    if adjustment.undone_at is not None:
        raise bad("already_undone", 409)
    by_id = {p.id: p for p in workshop.products}
    for row in adjustment.rows or []:
        product = by_id.get(row["id"])
        if product is not None:
            product.base = row["before"]
    adjustment.undone_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# =============================================================================
# Design templates
# =============================================================================

def _template_out(t: models.DesignTemplate) -> dict:
    return {"id": t.id, "name": t.name, "design": t.design, "createdAt": serializers.iso(t.created_at)}


@router.get("/templates")
def list_templates(workshop: models.Workshop = Depends(get_current_workshop), db: Session = Depends(get_db)):
    rows = (db.query(models.DesignTemplate).filter(models.DesignTemplate.workshop_id == workshop.id)
            .order_by(models.DesignTemplate.created_at.desc()).all())
    return [_template_out(t) for t in rows]


@router.post("/templates", status_code=201)
def create_template(body: schemas.TemplateIn, workshop: models.Workshop = Depends(get_current_workshop),
                    db: Session = Depends(get_db)):
    try:
        design = v.design(body.design)
    except ValueError as err:
        raise bad(str(err), 422)
    template = models.DesignTemplate(workshop_id=workshop.id, name=v.text(body.name, 80, True, "name"), design=design)
    db.add(template)
    db.commit()
    db.refresh(template)
    return _template_out(template)


@router.delete("/templates/{template_id}")
def delete_template(template_id: str, workshop: models.Workshop = Depends(get_current_workshop),
                    db: Session = Depends(get_db)):
    template = db.query(models.DesignTemplate).filter(
        models.DesignTemplate.id == template_id, models.DesignTemplate.workshop_id == workshop.id).first()
    if template is None:
        raise bad("template_not_found", 404)
    db.delete(template)
    db.commit()
    return {"ok": True}


# =============================================================================
# Orders — staff side
# =============================================================================

@router.get("/orders")
def list_orders(status: Optional[str] = Query(default=None, max_length=20),
                q: Optional[str] = Query(default=None, max_length=120),
                workshop: models.Workshop = Depends(get_current_workshop), db: Session = Depends(get_db)):
    query = db.query(models.Order).filter(models.Order.workshop_id == workshop.id)
    if status:
        query = query.filter(models.Order.status == status)
    if q and q.strip():
        like = f"%{q.strip()[:120]}%"
        query = query.filter(or_(models.Order.number.ilike(like), models.Order.customer_name.ilike(like),
                                 models.Order.title.ilike(like)))
    rows = query.order_by(models.Order.created_at.desc()).limit(300).all()
    return [serializers.order_out(o, "staff", light=True) for o in rows]


@router.post("/orders/{number}/quote", status_code=201)
def send_quote(number: str, body: schemas.QuoteIn, user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    order, _ = load_order(db, user, number, require="staff")
    if order.status in order_state.CLOSED_STATUSES:
        raise bad("order_closed", 409)
    check(f"quote:{order.workshop_id}", 30, 600)
    expected = None
    if body.expectedDate:
        if not valid_date_only(body.expectedDate):
            raise bad("invalid_date", 422)
        expected = body.expectedDate

    version = len(order.quotes) + 1
    quote = models.OrderQuote(order_id=order.id, version=version, amount=round(body.amount, 2), currency=order.currency,
                              reason=v.text(body.reason, 2000), expected_date=expected)
    db.add(quote)
    db.flush()
    add_event(db, order, "quote_sent", {"version": version, "amount": float(quote.amount)})
    order.status = recompute_status(order)
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "staff")


@router.post("/orders/{number}/proofs", status_code=201)
def create_proof(number: str, body: schemas.ProofIn, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Staff sends an artwork proof for the customer to approve. `body.lines` carries the
    updated design layers for the order lines they apply to; everything else on each line
    (color, size, quantity...) is untouched."""
    order, _ = load_order(db, user, number, require="staff")
    if order.status in order_state.CLOSED_STATUSES:
        raise bad("order_closed", 409)

    by_id = {line["id"]: dict(line) for line in (order.lines or [])}
    try:
        for item in body.lines:
            if item.id not in by_id:
                raise ValueError("unknown_line")
            by_id[item.id]["design"] = v.design(item.design)
    except ValueError as err:
        raise bad(str(err), 422)

    updated_lines = [by_id[line["id"]] for line in (order.lines or [])]
    order.lines = updated_lines
    version = len(order.proofs) + 1
    proof = models.OrderProof(order_id=order.id, version=version, lines=updated_lines, approved=False,
                              revision_requested=False)
    db.add(proof)
    db.flush()
    add_event(db, order, "proof_sent", {"version": version})
    order.status = recompute_status(order)
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "staff")


@router.post("/orders/{number}/status")
def change_status(number: str, body: schemas.StatusIn, user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    order, _ = load_order(db, user, number, require="staff")
    err = order_state.status_change_error(order.status, body.status, quote_dict(order), proof_dict(order))
    if err:
        raise bad(err, 409)

    if body.status in order_state.EXCEPTION_STATUSES and order.status not in order_state.EXCEPTION_STATUSES:
        order.resume_status = order.status       # remember what to go back to
    elif body.status not in order_state.EXCEPTION_STATUSES:
        order.resume_status = None

    order.status = body.status
    comment = v.text(body.comment, 2000)
    db.add(models.OrderStatusUpdate(order_id=order.id, status=body.status, comment=comment))
    add_event(db, order, "status_changed", {"status": body.status})
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "staff")


@router.put("/orders/{number}/schedule")
def update_schedule(number: str, body: schemas.ScheduleIn, user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    order, _ = load_order(db, user, number, require="staff")
    nxt = {"productionStart": body.productionStart or "", "productionDue": body.productionDue or "",
          "estimatedDate": body.estimatedDate or "", "promisedDate": body.promisedDate or ""}
    comment = v.text(body.comment, 2000)
    try:
        result = validate_schedule(order.schedule or {}, nxt, comment)
    except ValueError as err:
        raise bad(str(err), 422)

    order.schedule = {**(order.schedule or {}), **{k: val for k, val in nxt.items() if val}}
    if result["promiseChanged"]:
        add_message(db, order, "staff", "", code="promise_date_changed",
                   params={"date": nxt.get("promisedDate"), "comment": comment})
    add_event(db, order, "schedule_updated", {"promiseChanged": result["promiseChanged"]},
             visibility="public" if result["promiseChanged"] else "internal")
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "staff")


@router.post("/orders/{number}/notes", status_code=201)
def add_note(number: str, body: schemas.NoteIn, user: models.User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    order, _ = load_order(db, user, number, require="staff")
    db.add(models.OrderNote(order_id=order.id, text=v.text(body.text, 4000, True, "note")))
    db.commit()
    db.refresh(order)
    return serializers.order_out(order, "staff")


# --- CSV import of past / offline orders ------------------------------------

@router.post("/orders/import", status_code=201)
def import_orders(body: schemas.ImportIn, workshop: models.Workshop = Depends(get_current_workshop),
                  db: Session = Depends(get_db)):
    """Brings orders taken outside MadeTrace into the system as plain records (one line,
    no catalog match, no estimate). Staff can edit their status/price by hand afterwards."""
    check(f"import:{workshop.id}", 5, 3600)
    created = []
    for row in body.rows:
        if not v.csv_import_quantity(row.quantity):
            raise bad("invalid:quantity", 422)
        try:
            email = v.email(row.email)
        except ValueError as err:
            raise bad(str(err), 422)
        name = v.csv_cell(v.text(row.customerName, 120, True, "customer_name"))
        title = v.csv_cell(v.text(row.orderTitle, 200, True, "order_title"))
        quantity = int(row.quantity)

        def build(num: str, _name=name, _email=email, _title=title, _qty=quantity) -> models.Order:
            return models.Order(number=num, workshop_id=workshop.id, customer_id=None, source="manual",
                                customer_name=_name, customer_email=_email, title=_title, note="",
                                currency=workshop.currency, status="request",
                                lines=[{"id": "line-import", "name": _title, "quantity": _qty}],
                                estimate=None, attachments=[], schedule={}, last_read={})

        order = create_order_with_retry(db, build)
        add_event(db, order, "order_imported")
        created.append(order.number)
    db.commit()
    return {"ok": True, "created": created}


# =============================================================================
# Clients & reports
# =============================================================================

@router.get("/clients")
def clients(workshop: models.Workshop = Depends(get_current_workshop), db: Session = Depends(get_db)):
    rows = db.query(models.Order).filter(models.Order.workshop_id == workshop.id).all()
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    by_key: dict = {}
    for o in rows:
        key = (o.customer_email or "").lower() or f"name:{o.customer_name}"
        c = by_key.setdefault(key, {"name": o.customer_name, "email": o.customer_email, "orders": 0,
                                    "totalEstimate": 0.0, "lastOrderAt": epoch})
        c["orders"] += 1
        if o.estimate is not None:
            c["totalEstimate"] += float(o.estimate)
        if o.created_at and o.created_at > c["lastOrderAt"]:
            c["lastOrderAt"] = o.created_at
    out = sorted(by_key.values(), key=lambda c: c["lastOrderAt"], reverse=True)
    for c in out:
        c["totalEstimate"] = js_round(c["totalEstimate"] * 100) / 100
        c["lastOrderAt"] = serializers.iso(c["lastOrderAt"]) if c["lastOrderAt"] != epoch else None
    return out


@router.get("/reports")
def reports(workshop: models.Workshop = Depends(get_current_workshop), db: Session = Depends(get_db)):
    rows = db.query(models.Order).filter(models.Order.workshop_id == workshop.id).all()
    by_status: dict = {}
    revenue = 0.0
    completed_units = 0
    for o in rows:
        by_status[o.status] = by_status.get(o.status, 0) + 1
        if o.status == "completed":
            if o.estimate is not None:
                revenue += float(o.estimate)
            completed_units += sum(int(line.get("quantity", 0) or 0) for line in (o.lines or []))
    return {
        "totalOrders": len(rows),
        "byStatus": by_status,
        "completedRevenue": js_round(revenue * 100) / 100,
        "completedUnits": completed_units,
        "currency": workshop.currency,
    }
