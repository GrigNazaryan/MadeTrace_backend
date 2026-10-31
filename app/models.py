"""Database tables. All names are prefixed `mt_`, so this project can share a database with
older projects without touching their tables."""
import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .database import Base


def gen_id() -> str:
    return str(uuid.uuid4())


class UserRole:
    CUSTOMER = "customer"
    COMPANY = "company_owner"
    ADMIN = "admin"
    ALL = (CUSTOMER, COMPANY, ADMIN)


class User(Base):
    __tablename__ = "mt_users"

    id = Column(String(36), primary_key=True, default=gen_id)
    email = Column(String(254), nullable=False, unique=True, index=True)
    password_hash = Column(String(200), nullable=False)
    full_name = Column(String(120), nullable=False, default="")
    role = Column(String(20), nullable=False, default=UserRole.CUSTOMER, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    workshop = relationship("Workshop", back_populates="owner", uselist=False)

    # NOT a column: set per request when the token is an admin "log in as company" token.
    impersonated_by = None


class Workshop(Base):
    """A company / print workshop. Public fields feed the directory and storefront."""
    __tablename__ = "mt_workshops"

    id = Column(String(36), primary_key=True, default=gen_id)
    owner_id = Column(String(36), ForeignKey("mt_users.id", ondelete="CASCADE"), nullable=False, unique=True)

    name = Column(String(120), nullable=False)
    initials = Column(String(4), nullable=False, default="WS")
    theme = Column(String(20), nullable=False, default="theme-blue")
    country = Column(String(2), nullable=False, default="US")
    city = Column(String(80), nullable=False, default="")
    service_area = Column(String(160), nullable=False, default="")
    services = Column(JSONB, nullable=False, default=list)           # ["Screen printing", "DTF", ...]
    description = Column(String(400), nullable=False, default="")
    about = Column(Text, nullable=False, default="")
    pickup = Column(Boolean, nullable=False, default=True)
    is_public = Column(Boolean, nullable=False, default=False)       # opt-in directory listing
    public_contact = Column(JSONB, nullable=False, default=dict)     # {email, phone, website}
    payment_link = Column(String(500), nullable=True)                # the workshop's own payment link (we never touch product money)

    currency = Column(String(3), nullable=False, default="USD")
    timezone = Column(String(60), nullable=False, default="UTC")
    pricing = Column(JSONB, nullable=False, default=dict)            # estimate rules

    plan = Column(String(20), nullable=False, default="growth")      # core | growth
    subscription_status = Column(String(20), nullable=False, default="trial")  # trial | active | expired
    subscription_expires_at = Column(DateTime(timezone=True), nullable=True)
    is_blocked = Column(Boolean, nullable=False, default=False)

    tos_accepted_at = Column(DateTime(timezone=True), nullable=True)
    tos_version = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    owner = relationship("User", back_populates="workshop")
    products = relationship("Product", back_populates="workshop", cascade="all, delete-orphan",
                            order_by="Product.sort_order")


class Product(Base):
    __tablename__ = "mt_products"

    id = Column(String(36), primary_key=True, default=gen_id)
    workshop_id = Column(String(36), ForeignKey("mt_workshops.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(String(1000), nullable=False, default="")
    category = Column(String(60), nullable=False, default="Apparel")
    base = Column(Numeric(12, 2), nullable=False, default=0)                 # blank selling price
    acquisition_cost = Column(Numeric(12, 2), nullable=True)
    published = Column(Boolean, nullable=False, default=False)
    colors = Column(JSONB, nullable=False, default=list)                     # ["#f7f7f5", ...]
    sizes = Column(JSONB, nullable=False, default=list)                      # ["S", "M", ...]
    size_adjustments = Column(JSONB, nullable=False, default=dict)           # {"2XL": 2}
    mockup_type = Column(String(12), nullable=False, default="tee")          # tee|hoodie|tank|tote|reference
    print_areas = Column(JSONB, nullable=False, default=dict)                # {"front": {"widthCm": 32, "heightCm": 36}, ...}
    image_url = Column(String(600), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    workshop = relationship("Workshop", back_populates="products")


class Order(Base):
    __tablename__ = "mt_orders"

    id = Column(String(36), primary_key=True, default=gen_id)
    number = Column(String(20), nullable=False, unique=True, index=True)      # human number, e.g. MT-1042
    workshop_id = Column(String(36), ForeignKey("mt_workshops.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(String(36), ForeignKey("mt_users.id", ondelete="SET NULL"), nullable=True, index=True)
    source = Column(String(10), nullable=False, default="customer")           # customer | manual (CSV import)

    customer_name = Column(String(120), nullable=False, default="")
    customer_email = Column(String(254), nullable=False, default="")
    title = Column(String(200), nullable=False, default="")
    note = Column(Text, nullable=False, default="")
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(String(20), nullable=False, default="request", index=True)
    resume_status = Column(String(20), nullable=True)
    lines = Column(JSONB, nullable=False, default=list)
    estimate = Column(Numeric(12, 2), nullable=True)
    attachments = Column(JSONB, nullable=False, default=list)                 # [{id,name,size,type,url}]
    schedule = Column(JSONB, nullable=False, default=dict)
    last_read = Column(JSONB, nullable=False, default=dict)                   # {"staff": msgId, "customer": msgId}
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    quotes = relationship("OrderQuote", cascade="all, delete-orphan", order_by="OrderQuote.version")
    proofs = relationship("OrderProof", cascade="all, delete-orphan", order_by="OrderProof.version")
    messages = relationship("OrderMessage", cascade="all, delete-orphan", order_by="OrderMessage.created_at")
    events = relationship("OrderEvent", cascade="all, delete-orphan", order_by="OrderEvent.created_at")
    notes = relationship("OrderNote", cascade="all, delete-orphan", order_by="OrderNote.created_at")
    status_updates = relationship("OrderStatusUpdate", cascade="all, delete-orphan",
                                  order_by="OrderStatusUpdate.created_at")
    workshop = relationship("Workshop")


class OrderQuote(Base):
    __tablename__ = "mt_order_quotes"
    __table_args__ = (UniqueConstraint("order_id", "version", name="uq_mt_quote_version"),)

    id = Column(String(36), primary_key=True, default=gen_id)
    order_id = Column(String(36), ForeignKey("mt_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    reason = Column(Text, nullable=False, default="")
    expected_date = Column(String(10), nullable=True)
    decision = Column(String(12), nullable=True)          # accepted | declined | revision
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OrderProof(Base):
    __tablename__ = "mt_order_proofs"
    __table_args__ = (UniqueConstraint("order_id", "version", name="uq_mt_proof_version"),)

    id = Column(String(36), primary_key=True, default=gen_id)
    order_id = Column(String(36), ForeignKey("mt_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    lines = Column(JSONB, nullable=False, default=list)   # snapshot of the artwork at the time of the proof
    approved = Column(Boolean, nullable=False, default=False)
    revision_requested = Column(Boolean, nullable=False, default=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OrderMessage(Base):
    __tablename__ = "mt_order_messages"

    id = Column(String(36), primary_key=True, default=gen_id)
    order_id = Column(String(36), ForeignKey("mt_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    sender = Column(String(10), nullable=False)           # staff | customer
    text = Column(Text, nullable=False, default="")
    code = Column(String(40), nullable=True)              # system message (translated by the frontend)
    params = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OrderEvent(Base):
    __tablename__ = "mt_order_events"

    id = Column(String(36), primary_key=True, default=gen_id)
    order_id = Column(String(36), ForeignKey("mt_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(40), nullable=False)             # e.g. quote_sent — translated by the frontend
    params = Column(JSONB, nullable=False, default=dict)
    visibility = Column(String(10), nullable=False, default="public")   # public | internal
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OrderNote(Base):
    __tablename__ = "mt_order_notes"

    id = Column(String(36), primary_key=True, default=gen_id)
    order_id = Column(String(36), ForeignKey("mt_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OrderStatusUpdate(Base):
    __tablename__ = "mt_order_status_updates"

    id = Column(String(36), primary_key=True, default=gen_id)
    order_id = Column(String(36), ForeignKey("mt_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False)
    comment = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DesignTemplate(Base):
    __tablename__ = "mt_templates"

    id = Column(String(36), primary_key=True, default=gen_id)
    workshop_id = Column(String(36), ForeignKey("mt_workshops.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    design = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PriceAdjustment(Base):
    __tablename__ = "mt_price_adjustments"

    id = Column(String(36), primary_key=True, default=gen_id)
    workshop_id = Column(String(36), ForeignKey("mt_workshops.id", ondelete="CASCADE"), nullable=False, index=True)
    percent = Column(Numeric(8, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    rows = Column(JSONB, nullable=False, default=list)    # [{id,name,before,after,cost}]
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    undone_at = Column(DateTime(timezone=True), nullable=True)


class PaymentStatus:
    PENDING = "pending"      # request created, workshop has not paid yet
    SUBMITTED = "submitted"  # workshop says "I paid" — waiting for an admin check
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class SubscriptionPayment(Base):
    """A workshop paying the PLATFORM for its subscription (never buyer-to-seller money)."""
    __tablename__ = "mt_subscription_payments"

    id = Column(String(36), primary_key=True, default=gen_id)
    workshop_id = Column(String(36), ForeignKey("mt_workshops.id", ondelete="CASCADE"), nullable=False, index=True)
    plan = Column(String(20), nullable=False)
    months = Column(Integer, nullable=False, default=1)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(10), nullable=False, default="USD")
    reference = Column(String(20), nullable=False, unique=True, index=True)   # code the payer writes in the comment
    status = Column(String(20), nullable=False, default=PaymentStatus.PENDING, index=True)
    payer_note = Column(Text, nullable=True)
    receipt_url = Column(String(600), nullable=True)                           # screenshot / receipt
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)

    workshop = relationship("Workshop")


class AuditLog(Base):
    __tablename__ = "mt_audit_log"

    id = Column(String(36), primary_key=True, default=gen_id)
    admin_id = Column(String(36), nullable=True, index=True)
    action = Column(String(50), nullable=False, index=True)
    target_workshop_id = Column(String(36), nullable=True, index=True)
    target_user_id = Column(String(36), nullable=True)
    method = Column(String(10), nullable=True)
    path = Column(String(300), nullable=True)
    ip = Column(String(64), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
