"""Turns database rows into the JSON shapes the frontend uses (camelCase, same shapes as the prototype)."""
from typing import Optional

from . import models
from .domain.order_state import order_progress
from .subscriptions import effective_plan, is_live


def iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def f(value) -> Optional[float]:
    return None if value is None else float(value)


def product_out(p: models.Product, public: bool = False) -> dict:
    out = {
        "id": p.id,
        "name": p.name,
        "description": p.description or "",
        "category": p.category,
        "base": f(p.base),
        "published": bool(p.published),
        "colors": list(p.colors or []),
        "sizes": list(p.sizes or []),
        "sizeAdjustments": dict(p.size_adjustments or {}),
        "mockupType": p.mockup_type,
        "printAreas": dict(p.print_areas or {}),
        "image": p.image_url,
    }
    if not public:
        out["acquisitionCost"] = f(p.acquisition_cost)
    return out


def _contact(w: models.Workshop) -> dict:
    c = w.public_contact or {}
    return {"email": c.get("email", ""), "phone": c.get("phone", ""), "website": c.get("website", "")}


def workshop_card(w: models.Workshop) -> dict:
    """Directory card — public fields only."""
    return {
        "id": w.id, "name": w.name, "initials": w.initials, "theme": w.theme,
        "country": w.country, "city": w.city, "serviceArea": w.service_area,
        "services": list(w.services or []), "description": w.description, "pickup": bool(w.pickup),
        "currency": w.currency, "industry": "print", "public": True,
    }


def workshop_public(w: models.Workshop) -> dict:
    out = workshop_card(w)
    out.update({
        "about": w.about or "",
        "publicContact": _contact(w),
        "products": [product_out(p, public=True) for p in w.products if p.published],
        "pricing": dict(w.pricing or {}),
        "accepting": is_live(w),
    })
    return out


def workshop_private(w: models.Workshop) -> dict:
    out = workshop_public(w)
    out.update({
        "public": bool(w.is_public),
        "timezone": w.timezone,
        "paymentLink": w.payment_link or "",
        "products": [product_out(p, public=False) for p in w.products],
        "plan": w.plan,
        "effectivePlan": effective_plan(w),
        "subscriptionStatus": w.subscription_status,
        "subscriptionExpiresAt": iso(w.subscription_expires_at),
        "isBlocked": bool(w.is_blocked),
    })
    return out


def quote_out(q: models.OrderQuote) -> dict:
    return {"id": q.id, "version": q.version, "amount": f(q.amount), "currency": q.currency, "reason": q.reason or "",
            "expectedDate": q.expected_date, "decision": q.decision, "createdAt": iso(q.created_at),
            "decidedAt": iso(q.decided_at)}


def proof_out(p: Optional[models.OrderProof]) -> dict:
    if p is None:
        return {"version": 0, "approved": False}
    return {"version": p.version, "approved": bool(p.approved), "revisionRequested": bool(p.revision_requested),
            "lines": p.lines or [], "createdAt": iso(p.created_at), "approvedAt": iso(p.approved_at)}


def unread_count(o: models.Order, viewer: str) -> int:
    last = (o.last_read or {}).get(viewer)
    msgs = list(o.messages)
    start = 0
    if last:
        for i, m in enumerate(msgs):
            if m.id == last:
                start = i + 1
                break
    return sum(1 for m in msgs[start:] if m.sender != viewer)


def strip_design(lines: list) -> list:
    """List views do not need every artwork layer."""
    return [{k: v for k, v in line.items() if k != "design"} | {
        "hasDesign": bool((line.get("design") or {}).get("front") or (line.get("design") or {}).get("back"))
    } for line in (lines or [])]


def order_out(o: models.Order, viewer: str, light: bool = False) -> dict:
    """viewer: 'staff' (workshop / admin) or 'customer'."""
    staff = viewer == "staff"
    quotes = [quote_out(q) for q in o.quotes]
    proofs = list(o.proofs)
    current_proof = proofs[-1] if proofs else None
    proof = proof_out(current_proof)
    schedule = dict(o.schedule or {})
    if not staff:
        schedule = {"promisedDate": schedule.get("promisedDate")}
    progress = order_progress(o.status, o.resume_status, quotes[-1] if quotes else None, proof)
    w = o.workshop
    out = {
        "id": o.number,
        "workshopId": o.workshop_id,
        "workshop": {"id": w.id, "name": w.name, "initials": w.initials, "theme": w.theme, "city": w.city,
                     "country": w.country, "currency": w.currency,
                     "paymentLink": (w.payment_link or "") if o.status in ("approved", "proof", "production", "ready", "completed") else ""},
        "customer": {"name": o.customer_name, "email": o.customer_email},
        "title": o.title, "note": o.note or "", "currency": o.currency,
        "status": o.status, "resumeStatus": o.resume_status,
        "lines": strip_design(o.lines) if light else (o.lines or []),
        "estimate": f(o.estimate),
        "attachments": o.attachments or [],
        "schedule": schedule,
        "quotes": quotes,
        "proof": proof if not light else {k: v for k, v in proof.items() if k != "lines"},
        "progress": progress,
        "unread": unread_count(o, "staff" if staff else "customer"),
        "messageCount": len(o.messages),
        "source": o.source,
        "createdAt": iso(o.created_at),
        "updatedAt": iso(o.updated_at),
    }
    if light:
        last_event = o.events[-1] if o.events else None
        out["lastActivityAt"] = iso(last_event.created_at if last_event else o.created_at)
        return out
    out["proofVersions"] = [proof_out(p) for p in proofs[:-1]]
    out["messages"] = [{"id": m.id, "from": m.sender, "text": m.text or "", "code": m.code, "params": m.params or {},
                        "createdAt": iso(m.created_at)} for m in o.messages]
    out["history"] = [{"at": iso(e.created_at), "code": e.code, "params": e.params or {}, "visibility": e.visibility}
                      for e in o.events if staff or e.visibility != "internal"]
    out["statusUpdates"] = [{"status": s.status, "comment": s.comment, "at": iso(s.created_at)} for s in o.status_updates]
    if staff:
        out["internalNotes"] = [{"id": n.id, "text": n.text, "at": iso(n.created_at)} for n in o.notes]
    return out


def payment_out(p: models.SubscriptionPayment, admin: bool = False) -> dict:
    out = {
        "id": p.id, "reference": p.reference, "plan": p.plan, "months": p.months, "amount": f(p.amount),
        "currency": p.currency, "status": p.status, "payerNote": p.payer_note, "receiptUrl": p.receipt_url,
        "createdAt": iso(p.created_at), "submittedAt": iso(p.submitted_at), "confirmedAt": iso(p.confirmed_at),
    }
    if admin:
        out["workshopId"] = p.workshop_id
        out["workshopName"] = p.workshop.name if p.workshop else None
    return out


def me_out(user: models.User, workshop: Optional[models.Workshop] = None) -> dict:
    out = {"id": user.id, "email": user.email, "fullName": user.full_name, "role": user.role,
           "isImpersonated": bool(user.impersonated_by)}
    if workshop is not None:
        out["workshop"] = workshop_private(workshop)
    return out
