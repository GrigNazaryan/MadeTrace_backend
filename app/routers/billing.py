"""A workshop paying US for its MadeTrace subscription. Money for products never
passes through here — that is the workshop's own `payment_link`, paid directly by
the customer (see orders.py / serializers.order_out)."""
import secrets
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas, serializers
from ..config import (BILLING_CURRENCY, PLAN_PRICES, PLATFORM_PAYMENT_ADDRESS, PLATFORM_PAYMENT_HOLDER,
                      PLATFORM_PAYMENT_LINK, PLATFORM_PAYMENT_METHOD, PLATFORM_PAYMENT_NOTE,
                      SUBSCRIPTION_PERIOD_DAYS)
from ..database import get_db
from ..domain import validation as v
from ..models import PaymentStatus
from ..orders_common import bad
from ..ratelimit import check
from ..security import get_current_workshop
from ..subscriptions import effective_plan, is_live

router = APIRouter(prefix="/api/billing", tags=["billing"])

REFERENCE_ALPHABET = string.ascii_uppercase + string.digits


def _gen_reference(db: Session) -> str:
    for _ in range(20):
        candidate = "SUB-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        if not db.query(models.SubscriptionPayment).filter(models.SubscriptionPayment.reference == candidate).first():
            return candidate
    raise bad("could_not_allocate_reference", 500)


@router.get("/info")
def billing_info(workshop: models.Workshop = Depends(get_current_workshop)):
    details_ready = bool(PLATFORM_PAYMENT_ADDRESS or PLATFORM_PAYMENT_LINK)
    return {
        "currency": BILLING_CURRENCY,
        "periodDays": SUBSCRIPTION_PERIOD_DAYS,
        "plans": [{"id": pid, "price": price} for pid, price in PLAN_PRICES.items()],
        "subscription": {
            "plan": workshop.plan,
            "effectivePlan": effective_plan(workshop),
            "status": workshop.subscription_status,
            "expiresAt": serializers.iso(workshop.subscription_expires_at),
            "isLive": is_live(workshop),
        },
        "paymentDetails": {
            "ready": details_ready,
            "method": PLATFORM_PAYMENT_METHOD,
            "address": PLATFORM_PAYMENT_ADDRESS,
            "holder": PLATFORM_PAYMENT_HOLDER,
            "link": PLATFORM_PAYMENT_LINK,
            "note": PLATFORM_PAYMENT_NOTE,
        } if details_ready else {"ready": False},
    }


@router.get("/payments")
def list_payments(workshop: models.Workshop = Depends(get_current_workshop), db: Session = Depends(get_db)):
    rows = (db.query(models.SubscriptionPayment).filter(models.SubscriptionPayment.workshop_id == workshop.id)
            .order_by(models.SubscriptionPayment.created_at.desc()).all())
    return [serializers.payment_out(p) for p in rows]


@router.post("/payments", status_code=201)
def create_payment(body: schemas.PaymentCreateIn, workshop: models.Workshop = Depends(get_current_workshop),
                   db: Session = Depends(get_db)):
    check(f"billing_create:{workshop.id}", 10, 3600)
    amount = PLAN_PRICES[body.plan] * body.months
    payment = models.SubscriptionPayment(
        workshop_id=workshop.id, plan=body.plan, months=body.months, amount=amount, currency=BILLING_CURRENCY,
        reference=_gen_reference(db), status=PaymentStatus.PENDING,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return serializers.payment_out(payment)


@router.post("/payments/{payment_id}/submit")
def submit_payment(payment_id: str, body: schemas.PaymentSubmitIn, workshop: models.Workshop = Depends(get_current_workshop),
                   db: Session = Depends(get_db)):
    payment = db.query(models.SubscriptionPayment).filter(
        models.SubscriptionPayment.id == payment_id, models.SubscriptionPayment.workshop_id == workshop.id).first()
    if payment is None:
        raise bad("payment_not_found", 404)
    if payment.status != PaymentStatus.PENDING:
        raise bad("payment_not_pending", 409)
    try:
        receipt = v.storage_url(body.receiptUrl, "receipt") if body.receiptUrl else None
    except ValueError as err:
        raise bad(str(err), 422)
    payment.payer_note = v.text(body.payerNote, 500, True, "payer_note")
    payment.receipt_url = receipt
    payment.status = PaymentStatus.SUBMITTED
    payment.submitted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(payment)
    return serializers.payment_out(payment)


@router.post("/payments/{payment_id}/cancel")
def cancel_payment(payment_id: str, workshop: models.Workshop = Depends(get_current_workshop), db: Session = Depends(get_db)):
    payment = db.query(models.SubscriptionPayment).filter(
        models.SubscriptionPayment.id == payment_id, models.SubscriptionPayment.workshop_id == workshop.id).first()
    if payment is None:
        raise bad("payment_not_found", 404)
    if payment.status not in (PaymentStatus.PENDING, PaymentStatus.SUBMITTED):
        raise bad("payment_not_cancellable", 409)
    payment.status = PaymentStatus.CANCELLED
    db.commit()
    return {"ok": True}
