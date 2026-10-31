import hmac
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import models, schemas, serializers
from ..config import ADMIN_BOOTSTRAP_SECRET, CURRENT_TOS_VERSION, TRIAL_PERIOD_DAYS
from ..database import get_db
from ..domain import validation as v
from ..ratelimit import check
from ..security import (create_access_token, get_current_user, hash_password, verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])

THEMES = ("blue", "peach", "mint")
DEFAULT_PRICING = {"mode": "manual", "print": 0, "printSmall": 0, "printLarge": 0, "setup": 0,
                   "discountAt": 25, "discountPct": 0, "blank": 0}
COUNTRY_DEFAULTS = {  # country -> (currency, timezone)
    "US": ("USD", "America/New_York"), "GB": ("GBP", "Europe/London"), "DE": ("EUR", "Europe/Berlin"),
    "FR": ("EUR", "Europe/Paris"), "IT": ("EUR", "Europe/Rome"), "ES": ("EUR", "Europe/Madrid"),
    "IE": ("EUR", "Europe/Dublin"), "NL": ("EUR", "Europe/Amsterdam"), "PL": ("EUR", "Europe/Warsaw"),
    "PT": ("EUR", "Europe/Lisbon"), "AM": ("USD", "Asia/Yerevan"), "RU": ("USD", "Europe/Moscow"),
    "GE": ("USD", "Asia/Tbilisi"), "KZ": ("USD", "Asia/Almaty"), "AE": ("USD", "Asia/Dubai"),
    "TR": ("USD", "Europe/Istanbul"), "UA": ("USD", "Europe/Kyiv"), "BY": ("USD", "Europe/Minsk"),
}


def _initials(name: str) -> str:
    words = re.findall(r"\w+", name, flags=re.UNICODE)
    if not words:
        return "WS"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def _token_for(user: models.User) -> str:
    return create_access_token(user.id, user.role)


def _workshop_of(db: Session, user: models.User):
    if user.role != models.UserRole.COMPANY:
        return None
    return db.query(models.Workshop).filter(models.Workshop.owner_id == user.id).first()


@router.post("/register", status_code=201)
def register(body: schemas.RegisterIn, request: Request, db: Session = Depends(get_db)):
    check(f"register:{request.client.host if request.client else 'x'}", 10, 3600)
    email = body.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="email_taken")
    if body.role == "company":
        if body.company is None:
            raise HTTPException(status_code=422, detail="company_required")
        if not body.tosAccepted:
            raise HTTPException(status_code=422, detail="tos_required")
    try:
        pay_link = v.http_url(body.company.paymentLink, "payment_link") if body.company else None
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid:payment_link")

    user = models.User(
        email=email, password_hash=hash_password(body.password), full_name=body.fullName.strip(),
        role=models.UserRole.COMPANY if body.role == "company" else models.UserRole.CUSTOMER,
    )
    db.add(user)
    db.flush()

    if body.role == "company":
        c = body.company
        country = c.country.upper()
        currency, tz = COUNTRY_DEFAULTS.get(country, ("USD", "UTC"))
        now = datetime.now(timezone.utc)
        db.add(models.Workshop(
            owner_id=user.id, name=c.name.strip(), initials=_initials(c.name),
            theme=THEMES[sum(map(ord, c.name)) % len(THEMES)],
            country=country, city=c.city.strip(), service_area=(c.serviceArea or c.city).strip(),
            services=[s.strip()[:60] for s in c.services if s.strip()][:12],
            description=c.description.strip(), about="", pickup=True, is_public=False,
            public_contact={}, payment_link=pay_link, currency=currency, timezone=tz,
            pricing=dict(DEFAULT_PRICING),
            plan="growth", subscription_status="trial",
            subscription_expires_at=now + timedelta(days=TRIAL_PERIOD_DAYS),
            tos_accepted_at=now, tos_version=CURRENT_TOS_VERSION,
        ))
    db.commit()
    db.refresh(user)
    return {"accessToken": _token_for(user), "user": serializers.me_out(user, _workshop_of(db, user))}


@router.post("/login")
def login(body: schemas.LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.lower()
    check(f"login:{request.client.host if request.client else 'x'}:{email}", 10, 900)
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    return {"accessToken": _token_for(user), "user": serializers.me_out(user, _workshop_of(db, user))}


@router.post("/token", include_in_schema=True)
def token_form(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Form-style login used only by the Swagger 'Authorize' button at /docs."""
    user = db.query(models.User).filter(models.User.email == form.username.lower()).first()
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    return {"access_token": _token_for(user), "token_type": "bearer"}


@router.get("/me")
def me(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return serializers.me_out(user, _workshop_of(db, user))


@router.post("/change-password")
def change_password(body: schemas.PasswordChangeIn, user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if user.impersonated_by:
        raise HTTPException(status_code=403, detail="impersonation_read_only")
    check(f"pwd:{user.id}", 5, 900)
    if not verify_password(body.currentPassword, user.password_hash):
        raise HTTPException(status_code=400, detail="wrong_password")
    user.password_hash = hash_password(body.newPassword)
    db.commit()
    return {"ok": True}


@router.post("/bootstrap-admin")
def bootstrap_admin(body: schemas.BootstrapIn, user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """One-time: the signed-in user becomes admin when they know ADMIN_BOOTSTRAP_SECRET."""
    check(f"bootstrap:{user.id}", 5, 3600)
    if not ADMIN_BOOTSTRAP_SECRET or not hmac.compare_digest(body.secret, ADMIN_BOOTSTRAP_SECRET):
        raise HTTPException(status_code=403, detail="forbidden")
    if user.impersonated_by:
        raise HTTPException(status_code=403, detail="forbidden")
    if user.role == models.UserRole.COMPANY:
        raise HTTPException(status_code=409, detail="company_cannot_be_admin")
    user.role = models.UserRole.ADMIN
    db.commit()
    return {"accessToken": _token_for(user), "user": serializers.me_out(user)}
