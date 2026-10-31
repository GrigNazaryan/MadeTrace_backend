from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models, serializers
from ..config import (BILLING_CURRENCY, PLAN_PRICES, SUBSCRIPTION_PERIOD_DAYS, TRIAL_PERIOD_DAYS)
from ..database import get_db
from ..subscriptions import live_clause

router = APIRouter(prefix="/api", tags=["public"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    return {"status": "ok"}


@router.get("/directory")
def directory(db: Session = Depends(get_db)):
    """Every workshop that opted in to the directory and has a live subscription."""
    rows = (db.query(models.Workshop)
            .filter(models.Workshop.is_public.is_(True), live_clause())
            .order_by(models.Workshop.name).limit(500).all())
    return [serializers.workshop_card(w) for w in rows]


@router.get("/workshops/{workshop_id}")
def storefront(workshop_id: str, db: Session = Depends(get_db)):
    w = (db.query(models.Workshop)
         .filter(models.Workshop.id == workshop_id, models.Workshop.is_public.is_(True), live_clause()).first())
    if w is None:
        raise HTTPException(status_code=404, detail="workshop_not_found")
    return serializers.workshop_public(w)


@router.get("/billing/plans")
def plans():
    return {"currency": BILLING_CURRENCY, "periodDays": SUBSCRIPTION_PERIOD_DAYS, "trialDays": TRIAL_PERIOD_DAYS,
            "plans": [{"id": pid, "price": price} for pid, price in PLAN_PRICES.items()]}
