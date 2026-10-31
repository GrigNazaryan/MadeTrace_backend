import os
from datetime import timedelta

# --- Security / Auth ---------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = timedelta(hours=12)
IMPERSONATION_MINUTES = 60   # lifetime of an admin "log in as company" token; no refresh

# --- Database ----------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:Grig_2011@127.0.0.1:5432/madetrace",
)
IS_LOCAL_DB = ("127.0.0.1" in DATABASE_URL) or ("localhost" in DATABASE_URL)

if not SECRET_KEY:
    if IS_LOCAL_DB:
        SECRET_KEY = "local-development-only-secret"
    else:
        # Refuse to start with an empty / guessable secret on a real deployment.
        raise RuntimeError("SECRET_KEY environment variable is not set. Add it on Render (Environment).")

# --- CORS --------------------------------------------------------------------
FRONTEND_ORIGINS = [o.strip().rstrip("/") for o in os.getenv("FRONTEND_ORIGINS", "").split(",") if o.strip()]
# Vercel preview deployments (*.vercel.app) are allowed by default; tighten later if you wish.
FRONTEND_ORIGIN_REGEX = os.getenv("FRONTEND_ORIGIN_REGEX", r"https://.*\.vercel\.app")

# --- Business rules ----------------------------------------------------------
CURRENT_TOS_VERSION = "1.0"          # bump whenever the legal text changes
TRIAL_PERIOD_DAYS = 30               # every new workshop starts with a trial of the Growth plan
ADMIN_BOOTSTRAP_SECRET = os.getenv("ADMIN_BOOTSTRAP_SECRET", "")

# ===========================================================================
# SUBSCRIPTION PLANS (prices as shown on the Pricing page)
# ===========================================================================
SUBSCRIPTION_PERIOD_DAYS = 30        # one paid "month"
BILLING_CURRENCY = "USD"
PLAN_PRICES = {"core": 39, "growth": 59}   # per month, per workshop
GROWTH_ONLY_FEATURES = ("bulk_pricing",)

# ===========================================================================
# OUR PAYMENT DETAILS  <<<  FILL THESE IN WHEN YOU ARE READY  >>>
#
# This is where workshops send the money for their MadeTrace subscription.
# Put your 16-digit card number / IBAN / wallet between the quotes below, or set
# the same names as environment variables on Render (recommended: keeps it out of GitHub).
# Until PLATFORM_PAYMENT_ADDRESS or PLATFORM_PAYMENT_LINK is filled in, the payment
# window shows "payment details coming soon". Money for PRODUCTS never passes through us.
# ===========================================================================
PLATFORM_PAYMENT_METHOD = os.getenv("PLATFORM_PAYMENT_METHOD", "Bank card")   # e.g. "Bank card", "USDT (TRC20)", "IBAN"
PLATFORM_PAYMENT_ADDRESS = os.getenv("PLATFORM_PAYMENT_ADDRESS", "")          # card number / IBAN / wallet address
PLATFORM_PAYMENT_HOLDER = os.getenv("PLATFORM_PAYMENT_HOLDER", "")            # name on the card / account
PLATFORM_PAYMENT_LINK = os.getenv("PLATFORM_PAYMENT_LINK", "")                # optional payment link
PLATFORM_PAYMENT_NOTE = os.getenv("PLATFORM_PAYMENT_NOTE", "")                # optional extra instruction for payers
