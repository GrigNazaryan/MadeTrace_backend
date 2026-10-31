"""Request bodies. Heavy validation of design data lives in domain/validation.py."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class CompanyIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    country: str = Field(min_length=2, max_length=2)
    city: str = Field(min_length=1, max_length=80)
    serviceArea: str = Field(default="", max_length=160)
    services: List[str] = Field(default_factory=list, max_length=12)
    description: str = Field(default="", max_length=400)
    paymentLink: Optional[str] = Field(default=None, max_length=500)


class RegisterIn(BaseModel):
    role: Literal["customer", "company"]
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    fullName: str = Field(min_length=1, max_length=120)
    tosAccepted: bool = False
    company: Optional[CompanyIn] = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeIn(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=128)
    newPassword: str = Field(min_length=8, max_length=128)


class BootstrapIn(BaseModel):
    secret: str = Field(min_length=1, max_length=200)


class OrderCreateIn(BaseModel):
    workshopId: str
    lines: List[Dict[str, Any]] = Field(min_length=1, max_length=30)
    note: str = Field(default="", max_length=4000)
    customerName: Optional[str] = Field(default=None, max_length=120)
    attachments: List[Dict[str, Any]] = Field(default_factory=list, max_length=5)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DecisionIn(BaseModel):
    decision: Literal["accepted", "revision", "declined"]


class QuoteIn(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)
    reason: str = Field(default="", max_length=2000)
    expectedDate: Optional[str] = None


class ProofLineIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    design: Dict[str, Any] = Field(default_factory=dict)


class ProofIn(BaseModel):
    lines: List[ProofLineIn] = Field(min_length=1, max_length=30)


class StatusIn(BaseModel):
    status: str
    comment: str = Field(default="", max_length=2000)


class ScheduleIn(BaseModel):
    productionStart: Optional[str] = ""
    productionDue: Optional[str] = ""
    estimatedDate: Optional[str] = ""
    promisedDate: Optional[str] = ""
    comment: str = Field(default="", max_length=2000)


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    country: str = Field(min_length=2, max_length=2)
    city: str = Field(min_length=1, max_length=80)
    serviceArea: str = Field(default="", max_length=160)
    services: List[str] = Field(default_factory=list, max_length=12)
    description: str = Field(default="", max_length=400)
    about: str = Field(default="", max_length=6000)
    pickup: bool = True
    public: bool = False
    publicContact: Dict[str, str] = Field(default_factory=dict)
    paymentLink: Optional[str] = Field(default=None, max_length=500)


class PreferencesIn(BaseModel):
    timezone: str = Field(min_length=1, max_length=60)
    currency: str = Field(min_length=3, max_length=3)


class PricingIn(BaseModel):
    mode: Literal["auto", "manual"] = "auto"
    print: float = Field(ge=0, le=10_000_000)
    printSmall: float = Field(ge=0, le=10_000_000)
    printLarge: float = Field(ge=0, le=10_000_000)
    setup: float = Field(ge=0, le=10_000_000)
    discountAt: float = Field(ge=2, le=10_000_000)
    discountPct: float = Field(ge=0, le=100)
    productPrices: Dict[str, float] = Field(default_factory=dict)


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    category: str = Field(default="Apparel", max_length=60)
    base: float = Field(ge=0, le=10_000_000)
    acquisitionCost: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    published: bool = False
    colors: List[str] = Field(default_factory=list, max_length=24)
    sizes: List[str] = Field(default_factory=list, max_length=24)
    sizeAdjustments: Dict[str, float] = Field(default_factory=dict)
    mockupType: Literal["tee", "hoodie", "tank", "tote", "reference"] = "tee"
    printAreas: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    image: Optional[str] = Field(default=None, max_length=600)


class BulkPreviewIn(BaseModel):
    scope: Literal["all", "category", "selected"] = "all"
    category: str = ""
    selected: List[str] = Field(default_factory=list, max_length=500)
    percent: float
    rounding: float = 0.01


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    design: Dict[str, Any]


class ImportRow(BaseModel):
    customerName: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    orderTitle: str = Field(min_length=1, max_length=200)
    quantity: float


class ImportIn(BaseModel):
    rows: List[ImportRow] = Field(min_length=1, max_length=500)


class PaymentCreateIn(BaseModel):
    plan: Literal["core", "growth"]
    months: int = Field(ge=1, le=12)


class PaymentSubmitIn(BaseModel):
    payerNote: str = Field(min_length=3, max_length=500)
    receiptUrl: Optional[str] = Field(default=None, max_length=600)


class ImpersonationIn(BaseModel):
    readOnly: bool = True


class WorkshopAdminIn(BaseModel):
    plan: Optional[Literal["core", "growth"]] = None
