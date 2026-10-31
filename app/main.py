"""Application entrypoint. Run with: uvicorn app.main:app --host 0.0.0.0 --port $PORT"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models
from .config import FRONTEND_ORIGIN_REGEX, FRONTEND_ORIGINS
from .database import Base, engine
from .routers import admin, auth, billing, orders, public, workspace

app = FastAPI(title="MadeTrace API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS or ["*"],
    allow_origin_regex=FRONTEND_ORIGIN_REGEX or None,
    allow_credentials=False,          # we use a Bearer token, not cookies — no credentials needed
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(public.router)
app.include_router(orders.router)
app.include_router(workspace.router)
app.include_router(billing.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup() -> None:
    # Creates the mt_* tables if they don't exist yet. Safe to run on every boot:
    # it never touches tables that already exist, and the `mt_` prefix keeps this
    # project out of the way of any older project sharing the same database.
    Base.metadata.create_all(bind=engine)


@app.get("/")
def root():
    return {"service": "madetrace-api", "status": "ok"}
