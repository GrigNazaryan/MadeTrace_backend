"""Passwords, tokens and the dependencies that identify who is calling."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from . import models
from .audit import record_audit
from .config import ACCESS_TOKEN_EXPIRE, ALGORITHM, SECRET_KEY
from .database import get_db
from .subscriptions import sync_subscription

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str, role: str, expires: Optional[timedelta] = None,
                        extra_claims: Optional[dict] = None) -> str:
    payload = {"sub": user_id, "role": role, "exp": datetime.now(timezone.utc) + (expires or ACCESS_TOKEN_EXPIRE)}
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials",
                                 headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise unauthorized
    except JWTError:
        raise unauthorized

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None or not user.is_active:
        raise unauthorized

    # --- admin "log in as company" token ---------------------------------------
    imp_by = payload.get("imp_by")
    if imp_by:
        admin = (db.query(models.User)
                 .filter(models.User.id == imp_by, models.User.role == models.UserRole.ADMIN,
                         models.User.is_active.is_(True)).first())
        if admin is None:      # the token dies as soon as the issuing admin loses admin rights
            raise unauthorized
        if request.method not in SAFE_METHODS:
            if payload.get("ro", True):   # read-only unless the token explicitly says otherwise
                raise HTTPException(status_code=403, detail="impersonation_read_only")
            record_audit(db, admin_id=imp_by, action="impersonated_write", request=request, target_user_id=user.id)
        user.impersonated_by = imp_by
    return user


def require_roles(*roles: str):
    def dependency(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return user
    return dependency


require_admin = require_roles(models.UserRole.ADMIN)
require_customer = require_roles(models.UserRole.CUSTOMER)


def get_current_workshop(user: models.User = Depends(require_roles(models.UserRole.COMPANY)),
                         db: Session = Depends(get_db)) -> models.Workshop:
    workshop = db.query(models.Workshop).filter(models.Workshop.owner_id == user.id).first()
    if workshop is None:
        raise HTTPException(status_code=404, detail="workshop_not_found")
    if workshop.is_blocked and not user.impersonated_by:  # admins may still look inside blocked workshops
        raise HTTPException(status_code=403, detail="workshop_blocked")
    if sync_subscription(workshop):   # trial / paid period just ended
        db.commit()
        db.refresh(workshop)
    return workshop
