"""Tier-1 rule-based scoring engine (proposal 7.1). Pure, auditable, explainable.

Every service carries the exact signals that produced its score so the UI can
show *why* a category was assigned.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

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
    sender_email: str = ""
    # Per-sender evidence behind the score, used to write a specific explanation.
    facts: dict = field(default_factory=dict)


# ponytail: short list of two-part endings instead of the full Public Suffix List;
# add an ending here if a country's senders show up with the wrong name.
_TWO_PART_SUFFIXES = {
    "co.in", "net.in", "org.in", "gov.in", "ac.in", "co.uk", "org.uk", "ac.uk", "com.au",
    "net.au", "co.nz", "co.jp", "co.za", "com.br", "com.mx", "com.sg", "com.my", "com.cn",
}


def root_domain(domain: str) -> str:
    """mailer.ajio.in -> ajio.in, em.amazon.co.uk -> amazon.co.uk"""
    parts = domain.lower().strip(".").split(".")
    keep = 3 if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_SUFFIXES else 2
    return ".".join(parts[-keep:])


def _service_name(domain: str) -> str:
    return root_domain(domain).split(".")[0].capitalize()


def bucket(score: int) -> str:
    if score >= 70:
        return "Important"
    if score >= 40:
        return "Useful"
    if score >= 20:
        return "Optional"
    return "Marketing/Inactive"


CATEGORY_FLOORS = [("Important", 70), ("Useful", 40), ("Optional", 20), ("Marketing/Inactive", 0)]


def _clamp(score: int) -> int:
    return max(0, min(100, score))


def _what_if(signals: list[Signal], count: int, open_rate: float, replied: bool) -> list[dict]:
    """Things the user could do that would raise this sender's score."""
    fired = {s.label for s in signals}
    base = BASE_SCORE + sum(s.points for s in signals)
    out = []
    if "opened_frequently" not in fired:
        gain = POINTS["opened_frequently"] + (-POINTS["inactive_bulk"] if "inactive_bulk" in fired else 0)
        out.append({"action": "open", "points": gain, "score": _clamp(base + gain)})
    if not replied:
        gain = POINTS["replied"] + (-POINTS["inactive_bulk"] if "inactive_bulk" in fired else 0)
        out.append({"action": "reply", "points": gain, "score": _clamp(base + gain)})
    for item in out:
        item["category"] = bucket(item["score"])
    return out


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

    score = _clamp(BASE_SCORE + sum(s.points for s in signals))

    emails = [getattr(m, "sender_email", None) for m in messages if getattr(m, "sender_email", None)]
    if emails:
        primary_email = Counter(emails).most_common(1)[0][0]
    else:
        primary_email = domain

    keyword_hits: Counter = Counter()
    transactional_count = 0
    for m in messages:
        subject = (m.subject or "").lower()
        hits = [kw for kw in TRANSACTIONAL_KEYWORDS if kw in subject]
        if hits:
            transactional_count += 1
            keyword_hits.update(hits)

    recent_subjects: list[str] = []
    for m in sorted(messages, key=lambda m: m.date or datetime.min, reverse=True):
        subject = (m.subject or "").strip()
        if subject and subject not in recent_subjects:
            recent_subjects.append(subject)
        if len(recent_subjects) == 3:
            break

    first_days_ago = (now - min(dates)).days if dates else None
    months = max(1.0, (first_days_ago or 0) / 30)
    category = bucket(score)
    floor = dict(CATEGORY_FLOORS)[category]
    higher = next((c for c, f in reversed(CATEGORY_FLOORS) if f > floor), None)

    facts = {
        "read_count": read,
        "promo_count": round(promo_ratio * count),
        "unsub_count": round(unsub_ratio * count),
        "replied": replied,
        "transactional_count": transactional_count,
        "transactional_words": [kw for kw, _ in keyword_hits.most_common(4)],
        "recent_subjects": recent_subjects,
        "per_month": round(count / months, 1),
        "first_days_ago": first_days_ago,
        "next_category": higher,
        "points_to_next": dict(CATEGORY_FLOORS)[higher] - score if higher else None,
        "points_above_floor": score - floor,
        "what_if": _what_if(signals, count, open_rate, replied),
    }

    return ServiceScore(
        domain=domain,
        name=_service_name(domain),
        email_count=count,
        open_rate=round(open_rate, 3),
        days_since_last=days_since_last,
        has_unsubscribe=has_unsub,
        score=score,
        category=category,
        signals=signals,
        sender_email=primary_email,
        facts=facts,
    )


def score_all(messages: list, replied_domains: set[str]) -> list[ServiceScore]:
    groups: dict[str, list] = {}
    for m in messages:
        if m.sender_domain:
            groups.setdefault(m.sender_domain, []).append(m)
    scored = [score_service(d, msgs, replied_domains) for d, msgs in groups.items()]
    scored.sort(key=lambda s: (s.score, -s.email_count))
    return scored


# Inbox health + savings (proposal week 11). Tunable knobs, like the rubric above.
CATEGORY_VALUE = {"Important": 1.0, "Useful": 1.0, "Optional": 0.5, "Marketing/Inactive": 0.0}
RECENT_DAYS = 90
# ponytail: flat per-email triage cost; replace with pilot-measured timing.
SECONDS_PER_EMAIL = 10


def monthly_rates(messages: list, now: datetime | None = None) -> dict[str, float]:
    """Emails per month per domain, averaged over the last RECENT_DAYS."""
    cutoff = (now or datetime.utcnow()) - timedelta(days=RECENT_DAYS)
    counts = Counter(m.sender_domain for m in messages if m.sender_domain and m.date and m.date >= cutoff)
    return {domain: count * 30 / RECENT_DAYS for domain, count in counts.items()}


def inbox_insights(
    scored: list[ServiceScore],
    monthly: dict[str, float],
    decisions: dict[str, str],
    unsubscribed: set[str],
) -> dict:
    """Health score = value-weighted share of recent mail from senders still subscribed.

    Falls back to all-time counts when no mail arrived in the recent window.
    """
    active = [s for s in scored if s.domain not in unsubscribed]
    use_recent = sum(monthly.get(s.domain, 0) for s in active) > 0

    def volume(s: ServiceScore) -> float:
        return monthly.get(s.domain, 0) if use_recent else s.email_count

    def health_of(services: list[ServiceScore]) -> int:
        total = sum(volume(s) for s in services)
        if not total:
            return 100
        return round(100 * sum(CATEGORY_VALUE[s.category] * volume(s) for s in services) / total)

    # Senders the user could still drop: marketing ones not kept, plus the review list.
    candidates = [
        s for s in active
        if decisions.get(s.domain) != "keep"
        and (s.category == "Marketing/Inactive" or decisions.get(s.domain) == "unsubscribe")
    ]
    candidate_domains = {s.domain for s in candidates}

    total = sum(volume(s) for s in active)
    mix = {
        category: round(100 * sum(volume(s) for s in active if s.category == category) / total)
        if total else 0
        for category in CATEGORY_VALUE
    }

    reduction = sum(monthly.get(d, 0) for d in unsubscribed)
    potential = sum(monthly.get(s.domain, 0) for s in candidates)

    def minutes_per_year(per_month: float) -> int:
        return round(per_month * 12 * SECONDS_PER_EMAIL / 60)

    return {
        "health_score": health_of(active),
        # Score after unsubscribing from every remaining candidate.
        "potential_health_score": health_of([s for s in active if s.domain not in candidate_domains]),
        "candidate_count": len(candidates),
        "unsubscribed_count": len(unsubscribed),
        "mix": mix,  # % of recent mail per category, senders still subscribed
        "monthly_reduction": round(reduction),
        "minutes_saved_per_year": minutes_per_year(reduction),
        "potential_monthly_reduction": round(potential),
        "potential_minutes_saved_per_year": minutes_per_year(potential),
        "window_days": RECENT_DAYS,
        "seconds_per_email": SECONDS_PER_EMAIL,
    }
