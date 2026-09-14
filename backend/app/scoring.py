"""Tier-1 rule-based scoring engine (proposal 7.1). Pure, auditable, explainable.

Every service carries the exact signals that produced its score so the UI can
show *why* a category was assigned.
"""
from dataclasses import dataclass, field
from datetime import datetime

# Rubric from proposal 7.1. Tunable knobs — calibrate against pilot decisions.
BASE_SCORE = 50  # ponytail: neutral midpoint; shift if pilot agreement skews.
PROMOTIONAL_LABEL = "CATEGORY_PROMOTIONS"
PROMO_RATIO = 0.5
UNSUB_RATIO = 0.5
OPEN_RATE_FREQUENT = 0.5
INACTIVE_MIN_COUNT = 50
INACTIVE_MAX_OPEN = 0.1

TRANSACTIONAL_KEYWORDS = (
    "order", "receipt", "invoice", "payment", "statement", "confirm",
    "shipped", "shipping", "delivery", "verify", "verification", "otp",
    "code", "booking", "ticket", "reservation", "refund", "purchase",
)

POINTS = {
    "promotional": -30,
    "has_unsubscribe": -20,
    "replied": 40,
    "transactional": 35,
    "opened_frequently": 25,
    "inactive_bulk": -40,
}

SIGNAL_LABELS = {
    "promotional": "Marked promotional by Gmail",
    "has_unsubscribe": "Has List-Unsubscribe header",
    "replied": "You have replied to this sender",
    "transactional": "Transactional keywords in subjects",
    "opened_frequently": "You open these often",
    "inactive_bulk": "50+ emails, almost never opened",
}


@dataclass
class Signal:
    label: str
    points: int


@dataclass
class ServiceScore:
    domain: str
    name: str
    email_count: int
    open_rate: float
    days_since_last: int | None
    has_unsubscribe: bool
    score: int
    category: str
    signals: list[Signal] = field(default_factory=list)


def _service_name(domain: str) -> str:
    parts = domain.split(".")
    core = parts[-2] if len(parts) >= 2 else domain
    return core.capitalize()


def bucket(score: int) -> str:
    if score >= 70:
        return "Important"
    if score >= 40:
        return "Useful"
    if score >= 20:
        return "Optional"
    return "Marketing/Inactive"


def score_service(
    domain: str,
    messages: list,
    replied_domains: set[str],
    now: datetime | None = None,
) -> ServiceScore:
    now = now or datetime.utcnow()
    count = len(messages)
    read = sum(1 for m in messages if not m.is_unread)
    open_rate = read / count if count else 0.0

    dates = [m.date for m in messages if m.date]
    days_since_last = (now - max(dates)).days if dates else None

    promo_ratio = sum(1 for m in messages if PROMOTIONAL_LABEL in (m.labels or "")) / count
    unsub_ratio = sum(1 for m in messages if m.list_unsubscribe) / count
    has_unsub = unsub_ratio >= UNSUB_RATIO
    transactional = any(
        kw in (m.subject or "").lower() for m in messages for kw in TRANSACTIONAL_KEYWORDS
    )
    replied = domain in replied_domains

    signals: list[Signal] = []

    def add(key: str):
        signals.append(Signal(key, POINTS[key]))

    if promo_ratio >= PROMO_RATIO:
        add("promotional")
    if has_unsub:
        add("has_unsubscribe")
    if replied:
        add("replied")
    if transactional:
        add("transactional")
    if open_rate >= OPEN_RATE_FREQUENT:
        add("opened_frequently")
    if count >= INACTIVE_MIN_COUNT and open_rate < INACTIVE_MAX_OPEN and not replied:
        add("inactive_bulk")

    score = BASE_SCORE + sum(s.points for s in signals)
    score = max(0, min(100, score))

    return ServiceScore(
        domain=domain,
        name=_service_name(domain),
        email_count=count,
        open_rate=round(open_rate, 3),
        days_since_last=days_since_last,
        has_unsubscribe=has_unsub,
        score=score,
        category=bucket(score),
        signals=signals,
    )


def score_all(messages: list, replied_domains: set[str]) -> list[ServiceScore]:
    groups: dict[str, list] = {}
    for m in messages:
        if m.sender_domain:
            groups.setdefault(m.sender_domain, []).append(m)
    scored = [score_service(d, msgs, replied_domains) for d, msgs in groups.items()]
    scored.sort(key=lambda s: (s.score, -s.email_count))
    return scored
