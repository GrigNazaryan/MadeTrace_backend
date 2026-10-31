"""Platform admin console: manage workshops, confirm/reject subscription payments,
issue "log in as company" tokens, and read the audit trail those tokens leave behind."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models, schemas, serializers
from ..audit import record_audit
from ..config import TRIAL_PERIOD_DAYS, IMPERSONATION_MINUTES, SUBSCRIPTION_PERIOD_DAYS
from ..database import get_db
from ..models import PaymentStatus, UserRole
from ..orders_common import bad
from ..ratelimit import check
from ..security import create_access_token, require_admin
from ..subscriptions import is_live, sync_subscription

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _workshop_admin_out(w: models.Workshop) -> dict:
    out = serializers.workshop_private(w)
    out["ownerId"] = w.owner_id
    out["ownerEmail"] = w.owner.email if w.owner else None
    return out


# =============================================================================
# Workshops
# =============================================================================

@router.get("/workshops")
def list_workshops(status: Optional[str] = Query(default=None, max_length=20),
                   plan: Optional[str] = Query(default=None, max_length=20),
                   blocked: Optional[bool] = Query(default=None),
                   q: Optional[str] = Query(default=None, max_length=120),
                   db: Session = Depends(get_db)):
    query = db.query(models.Workshop)
    if status:
        query = query.filter(models.Workshop.subscription_status == status)
    if plan:
        query = query.filter(models.Workshop.plan == plan)
    if blocked is not None:
        query = query.filter(models.Workshop.is_blocked.is_(blocked))
    if q and q.strip():
        like = f"%{q.strip()[:120]}%"
        query = query.filter(models.Workshop.name.ilike(like))
    rows = query.order_by(models.Workshop.created_at.desc()).limit(500).all()
    changed = False
    for w in rows:
        changed = sync_subscription(w) or changed
    if changed:
        db.commit()
    return [_workshop_admin_out(w) for w in rows]


@router.get("/workshops/{workshop_id}")
def get_workshop(workshop_id: str, db: Session = Depends(get_db)):
    w = db.query(models.Workshop).filter(models.Workshop.id == workshop_id).first()
    if w is None:
        raise bad("workshop_not_found", 404)
    if sync_subscription(w):
        db.commit()
        db.refresh(w)
    return _workshop_admin_out(w)


@router.put("/workshops/{workshop_id}")
def set_plan(workshop_id: str, body: schemas.WorkshopAdminIn, admin: models.User = Depends(require_admin),
            db: Session = Depends(get_db)):
    w = db.query(models.Workshop).filter(models.Workshop.id == workshop_id).first()
    if w is None:
        raise bad("workshop_not_found", 404)
    if body.plan is not None:
        w.plan = body.plan
        record_audit(db, admin_id=admin.id, action="plan_changed", target_workshop_id=w.id, detail=body.plan)
    db.commit()
    db.refresh(w)
    return _workshop_admin_out(w)


@router.post("/workshops/{workshop_id}/trial")
def grant_trial(workshop_id: str, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    """Resets the workshop to a fresh Growth trial — e.g. for a goodwill extension."""
    w = db.query(models.Workshop).filter(models.Workshop.id == workshop_id).first()
    if w is None:
        raise bad("workshop_not_found", 404)
    w.subscription_status = "trial"
    w.plan = "growth"
    w.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=TRIAL_PERIOD_DAYS)
    record_audit(db, admin_id=admin.id, action="trial_granted", target_workshop_id=w.id)
    db.commit()
    db.refresh(w)
    return _workshop_admin_out(w)


@router.post("/workshops/{workshop_id}/block")
def block_workshop(workshop_id: str, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    w = db.query(models.Workshop).filter(models.Workshop.id == workshop_id).first()
    if w is None:
        raise bad("workshop_not_found", 404)
    w.is_blocked = True
    record_audit(db, admin_id=admin.id, action="workshop_blocked", target_workshop_id=w.id)
    db.commit()
    db.refresh(w)
    return _workshop_admin_out(w)


@router.post("/workshops/{workshop_id}/unblock")
def unblock_workshop(workshop_id: str, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    w = db.query(models.Workshop).filter(models.Workshop.id == workshop_id).first()
    if w is None:
        raise bad("workshop_not_found", 404)
    w.is_blocked = False
    record_audit(db, admin_id=admin.id, action="workshop_unblocked", target_workshop_id=w.id)
    db.commit()
    db.refresh(w)
    return _workshop_admin_out(w)


# =============================================================================
# Subscription payments
# =============================================================================

@router.get("/payments")
def list_payments(status: Optional[str] = Query(default=None, max_length=20), db: Session = Depends(get_db)):
    query = db.query(models.SubscriptionPayment)
    if status:
        query = query.filter(models.SubscriptionPayment.status == status)
    rows = query.order_by(models.SubscriptionPayment.created_at.desc()).limit(500).all()
    return [serializers.payment_out(p, admin=True) for p in rows]


@router.post("/payments/{payment_id}/confirm")
def confirm_payment(payment_id: str, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    payment = db.query(models.SubscriptionPayment).filter(models.SubscriptionPayment.id == payment_id).first()
    if payment is None:
        raise bad("payment_not_found", 404)
    if payment.status not in (PaymentStatus.PENDING, PaymentStatus.SUBMITTED):
        raise bad("payment_not_pending", 409)
    workshop = payment.workshop
    now = datetime.now(timezone.utc)
    base = workshop.subscription_expires_at if (workshop.subscription_expires_at and workshop.subscription_expires_at > now) else now
    workshop.subscription_expires_at = base + timedelta(days=SUBSCRIPTION_PERIOD_DAYS * payment.months)
    workshop.plan = payment.plan
    workshop.subscription_status = "active"
    workshop.is_blocked = False
    payment.status = PaymentStatus.CONFIRMED
    payment.confirmed_at = now
    record_audit(db, admin_id=admin.id, action="payment_confirmed", target_workshop_id=workshop.id,
                detail=payment.reference)
    db.commit()
    db.refresh(payment)
    return serializers.payment_out(payment, admin=True)


@router.post("/payments/{payment_id}/reject")
def reject_payment(payment_id: str, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    payment = db.query(models.SubscriptionPayment).filter(models.SubscriptionPayment.id == payment_id).first()
    if payment is None:
        raise bad("payment_not_found", 404)
    if payment.status not in (PaymentStatus.PENDING, PaymentStatus.SUBMITTED):
        raise bad("payment_not_pending", 409)
    payment.status = PaymentStatus.REJECTED
    record_audit(db, admin_id=admin.id, action="payment_rejected", target_workshop_id=payment.workshop_id,
                detail=payment.reference)
    db.commit()
    db.refresh(payment)
    return serializers.payment_out(payment, admin=True)


# =============================================================================
# "Log in as company"
# =============================================================================

@router.post("/workshops/{workshop_id}/impersonate")
def impersonate_workshop(workshop_id: str, body: schemas.ImpersonationIn, admin: models.User = Depends(require_admin),
                         db: Session = Depends(get_db)):
    w = db.query(models.Workshop).filter(models.Workshop.id == workshop_id).first()
    if w is None:
        raise bad("workshop_not_found", 404)
    owner = db.query(models.User).filter(models.User.id == w.owner_id, models.User.role == UserRole.COMPANY).first()
    if owner is None or not owner.is_active:
        raise bad("owner_not_found", 404)
    check(f"impersonate:{admin.id}", 20, 3600)
    token = create_access_token(owner.id, owner.role, expires=timedelta(minutes=IMPERSONATION_MINUTES),
                                extra_claims={"imp_by": admin.id, "ro": body.readOnly})
    record_audit(db, admin_id=admin.id, action="impersonation_started", target_workshop_id=w.id,
                target_user_id=owner.id, detail="read_only" if body.readOnly else "read_write")
    return {"accessToken": token, "readOnly": body.readOnly, "expiresInMinutes": IMPERSONATION_MINUTES,
           "workshop": _workshop_admin_out(w)}


# =============================================================================
# Audit log & stats
# =============================================================================

@router.get("/audit-log")
def audit_log(workshop_id: Optional[str] = Query(default=None), limit: int = Query(default=200, ge=1, le=500),
             db: Session = Depends(get_db)):
    query = db.query(models.AuditLog)
    if workshop_id:
        query = query.filter(models.AuditLog.target_workshop_id == workshop_id)
    rows = query.order_by(models.AuditLog.created_at.desc()).limit(limit).all()
    return [{"id": r.id, "adminId": r.admin_id, "action": r.action, "targetWorkshopId": r.target_workshop_id,
            "targetUserId": r.target_user_id, "method": r.method, "path": r.path, "ip": r.ip, "detail": r.detail,
            "createdAt": serializers.iso(r.created_at)} for r in rows]


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    workshops = db.query(models.Workshop).all()
    by_status: dict = {}
    by_plan: dict = {}
    live_count = 0
    for w in workshops:
        by_status[w.subscription_status] = by_status.get(w.subscription_status, 0) + 1
        by_plan[w.plan] = by_plan.get(w.plan, 0) + 1
        if is_live(w):
            live_count += 1
    total_orders = db.query(models.Order).count()
    pending_payments = db.query(models.SubscriptionPayment).filter(
        models.SubscriptionPayment.status.in_([PaymentStatus.PENDING, PaymentStatus.SUBMITTED])).count()
    mrr = sum((w.plan == "growth" and 59 or 39) for w in workshops if is_live(w))  # rough estimate at list price
    return {
        "totalWorkshops": len(workshops), "liveWorkshops": live_count, "byStatus": by_status, "byPlan": by_plan,
        "totalOrders": total_orders, "pendingPayments": pending_payments, "estimatedMrr": mrr,
    }
