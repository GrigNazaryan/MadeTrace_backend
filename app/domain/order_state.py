"""Order workflow rules (pure Python, no framework).

Ported from the MadeTrace prototype (order-state.js) and made authoritative on the server:
the browser can never move an order into a state these rules forbid.
Errors are machine-readable codes; the frontend translates them.
"""
from typing import Optional

STAGE_NAMES = ("Request Received", "Review & Approval", "In Production", "Ready / Completed")
EXCEPTION_STATUSES = ("waiting", "delayed", "cancelled")
ALL_STATUSES = (
    "request", "clarification", "quoted", "approved", "proof",
    "production", "ready", "completed", "waiting", "delayed", "cancelled",
)
CLOSED_STATUSES = ("completed", "cancelled")
NO_QUOTE_OR_PROOF_STATUSES = ("production", "ready", "completed", "cancelled")


def order_progress(status: str, resume_status: Optional[str], quote: Optional[dict], proof: Optional[dict]) -> dict:
    base = (resume_status or "request") if status in EXCEPTION_STATUSES else status
    stage = {"request": 0, "clarification": 1, "quoted": 1, "approved": 1, "proof": 1,
             "production": 2, "ready": 3, "completed": 3}.get(base, 0)
    revision = status == "clarification" and (
        (quote or {}).get("decision") == "revision" or (proof or {}).get("revisionRequested")
    )
    exception = "revision" if revision else (status if status in EXCEPTION_STATUSES else None)
    return {"stage": stage, "exception": exception, "complete": status == "completed"}


def status_change_error(status: str, nxt: str, quote: Optional[dict], proof: Optional[dict]) -> Optional[str]:
    """Returns an error code, or None when the transition is allowed."""
    if nxt not in ALL_STATUSES:
        return "unknown_status"
    if status == "completed" and nxt != "completed":
        return "order_completed"
    if status == "cancelled" and nxt != "cancelled":
        return "order_cancelled"
    accepted = bool(quote) and quote.get("decision") == "accepted"
    if nxt == "quoted" and not quote:
        return "quote_required"
    if nxt in ("approved", "proof", "production", "ready", "completed") and not accepted:
        return "quote_not_accepted"
    has_proof = bool(proof) and bool(proof.get("version"))
    if nxt == "proof" and not has_proof:
        return "proof_required"
    if nxt in ("production", "ready", "completed") and has_proof and not proof.get("approved"):
        return "proof_not_approved"
    return None


def approval_status(status: str, quote: Optional[dict], proof: Optional[dict], has_quotes: bool) -> str:
    if status in ("production", "ready", "completed", "cancelled", "waiting", "delayed"):
        return status
    if (quote or {}).get("decision") == "revision" or (proof or {}).get("revisionRequested"):
        return "clarification"
    if (quote or {}).get("decision") != "accepted":
        return "quoted" if has_quotes else "request"
    if proof and proof.get("version") and not proof.get("approved"):
        return "proof"
    return "approved"
