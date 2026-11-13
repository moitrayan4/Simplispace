import { memo, useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { checkOnline, useOnline } from "./online";
import "./App.css";

const CATEGORY_CLASS = {
  Important: "cat-important",
  Useful: "cat-useful",
  Optional: "cat-optional",
  "Marketing/Inactive": "cat-marketing",
};

const PAGE_SIZE = 9;
const AUTO_SYNC_MS = 30 * 60 * 1000;

// Page numbers to show (0-based), with null for "…": first, last, and two either side.
function pageItems(page, count) {
  const keep = new Set([0, count - 1]);
  for (let i = page - 2; i <= page + 2; i++) if (i >= 0 && i < count) keep.add(i);
  const items = [];
  [...keep].sort((a, b) => a - b).forEach((p, i, list) => {
    if (i > 0 && p - list[i - 1] > 1) items.push(null);
    items.push(p);
  });
  return items;
}

function Pager({ page, count, onGo }) {
  const [typed, setTyped] = useState("");
  const go = (p) => onGo(Math.min(count - 1, Math.max(0, p)));
  function jump(e) {
    e.preventDefault();
    const n = parseInt(typed, 10);
    if (n >= 1 && n <= count) go(n - 1);
    setTyped("");
  }
  return (
    <nav className="pager" aria-label="Card pages">
      <button className="pager-btn" onClick={() => go(0)} disabled={page === 0} aria-label="First page" title="First page">«</button>
      <button className="pager-btn" onClick={() => go(page - 1)} disabled={page === 0} aria-label="Previous page" title="Previous page">‹</button>
      {pageItems(page, count).map((p, i) =>
        p === null ? (
          <span key={`gap${i}`} className="pager-gap">…</span>
        ) : (
          <button
            key={p}
            className={`pager-btn ${p === page ? "current" : ""}`}
            onClick={() => go(p)}
            aria-current={p === page ? "page" : undefined}
          >
            {p + 1}
          </button>
        ),
      )}
      <button className="pager-btn" onClick={() => go(page + 1)} disabled={page >= count - 1} aria-label="Next page" title="Next page">›</button>
      <button className="pager-btn" onClick={() => go(count - 1)} disabled={page >= count - 1} aria-label="Last page" title="Last page">»</button>
      <form className="pager-jump" onSubmit={jump}>
        <label htmlFor="page-jump" className="muted small">Go to</label>
        <input
          id="page-jump"
          type="number"
          min="1"
          max={count}
          inputMode="numeric"
          placeholder={`1–${count}`}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
        />
      </form>
    </nav>
  );
}

function timeAgo(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

// Red, blue, yellow, green: the order cards cycle through in the mixed view.
const MIX_ORDER = ["Marketing/Inactive", "Useful", "Optional", "Important"];

const ORDER_GROUPS = {
  "Sort all senders": {
    mixed: "All categories",
    spam: "Most likely spam first",
    important: "Most important first",
  },
  "Show one category": {
    "only:Important": "Important senders only",
    "only:Useful": "Useful senders only",
    "only:Optional": "Optional senders only",
    "only:Marketing/Inactive": "Marketing & inactive only",
  },
};

// `services` arrives sorted by score, lowest first.
function orderServices(services, order) {
  if (order === "spam") return services;
  if (order === "important") return [...services].reverse();
  if (order.startsWith("only:")) return services.filter((s) => s.category === order.slice(5));
  const groups = MIX_ORDER.map((category) => services.filter((s) => s.category === category));
  const mixed = [];
  for (let i = 0; mixed.length < services.length; i++) {
    for (const group of groups) if (i < group.length) mixed.push(group[i]);
  }
  return mixed;
}

const PHASE_LABEL = {
  starting: "Starting…",
  listing: "Finding your emails…",
  checking: "Checking for new emails…",
  fetching: "Fetching messages",
  scanning_sent: "Checking your replies…",
  done: "Scoring…",
  background: "Updating in background…",
};

function ProgressBar({ progress }) {
  const p = progress || {};
  const isComplete = Boolean(p.first_page_done || p.phase === "done" || p.phase === "background");
  const label = isComplete ? "Scoring…" : PHASE_LABEL[p.phase] || "Working…";
  const hasTotal = p.phase === "fetching" && p.total > 0;
  const pct = isComplete ? 100 : hasTotal ? Math.round((p.done / p.total) * 100) : null;
  return (
    <div className="progress">
      <div className="progress-head">
        <span>{label}</span>
        <span className="muted">{pct !== null ? `${pct}%` : ""}</span>
      </div>
      <div className="progress-track">
        <div
          className={"progress-fill" + (pct !== null ? "" : " indeterminate")}
          style={pct !== null ? { width: pct + "%" } : undefined}
        />
      </div>
    </div>
  );
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
    </svg>
  );
}

function LoginScreen() {
  const online = useOnline();
  return (
    <div className="login">
      <div className="login-card">
        <span className="logo-badge lg">
          <img src="/logo-mark.png" alt="Simplispace" />
        </span>
        <h1 className="login-title">Simplispace</h1>
        <p className="login-sub">
          Your inbox has a lot going on. Let’s find out who actually emails you,
          and quietly clean up the rest.
        </p>

        {online ? (
          <a className="btn google" href={api.loginUrl()}>
            <GoogleIcon />
            <span>Continue with Google</span>
          </a>
        ) : (
          <button className="btn google" disabled>
            <GoogleIcon />
            <span>Continue with Google</span>
          </button>
        )}

        {online ? (
          <p className="login-fine">Sign in with Google to get started.</p>
        ) : (
          <p className="error offline-inline" role="alert">
            No internet connection. Connect to the internet to sign in; this page updates by itself.
          </p>
        )}
      </div>
    </div>
  );
}

function Check() {
  return (
    <span className="check">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M5 13l4 4L19 7" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

function PrivacyNotice({ email, onAccept }) {
  return (
    <div className="login">
      <div className="login-card notice">
        <span className="logo-badge lg">
          <img src="/logo-mark.png" alt="Simplispace" />
        </span>
        <h1 className="login-title">A quick note on privacy</h1>
        <p className="login-sub">
          You’re signed in as {email}. Here’s exactly what Simplispace does with your account.
        </p>

        <ul className="notice-points">
          <li><Check /><span>We only read your mail. We never send or delete anything.</span></li>
          <li><Check /><span>Nothing gets unsubscribed until you say so.</span></li>
          <li><Check /><span>What your emails say never leaves your inbox.</span></li>
        </ul>

        <button className="btn primary wide" onClick={onAccept}>Continue</button>
      </div>
    </div>
  );
}

function SkeletonTable() {
  return (
    <div className="skeleton-card">
      {Array.from({ length: 7 }).map((_, i) => (
        <div className="skeleton-row" key={i}>
          <div className="sk sk-name" />
          <div className="sk sk-badge" />
          <div className="sk sk-chip" />
          <div className="sk sk-sm" />
          <div className="sk sk-sm" />
        </div>
      ))}
    </div>
  );
}

const VIA_LABELS = {
  one_click: "Unsubscribed with a one-click request.",
  link: "Unsubscribe page opened in your browser. Finish there if it asks.",
  mailto: "Unsubscribe email opened in your mail app. Send it to finish.",
  browser: "Unsubscribe request sent from your browser. Check the page it opened.",
  in_app: "Unsubscribe page opened in Simplispace.",
  gmail: "Unsubscribed with Gmail’s Unsubscribe button.",
  block: "Opened in Gmail to block the sender.",
};

const CONFIRMED = ["confirmed", "page_confirmed"];

const STATUS_SHORT = {
  in_progress: "In progress",
  checking: "Checking",
  needs_answer: "Waiting for you",
  confirmed: "✓ Confirmed",
  page_confirmed: "✓ Confirmed",
  page_failed: "Didn’t work",
  still_sending: "Still emailing",
};

function shortDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

// Whether an unsubscribe actually worked, judged from the mailbox after syncs.
const ASK_BY_VIA = {
  gmail: "Did you click Unsubscribe in Gmail?",
  block: "Did you block this sender in Gmail?",
  mailto: "Did you send the unsubscribe email?",
  browser: "Did the page in your browser confirm you’re unsubscribed?",
  link: "Did the page in your browser confirm you’re unsubscribed?",
};

function UnsubscribeStatus({ decision, onRetry, onReset, onAnswer }) {
  if (decision.status === "page_confirmed") {
    return <p className="decision-note done">✓ Unsubscribed. The sender’s page confirmed it.</p>;
  }
  if (decision.status === "in_progress") {
    return (
      <p className="decision-note checking">
        Finish in the window that opened. Simplispace will notice when it’s done…
      </p>
    );
  }
  if (decision.status === "needs_answer") {
    return (
      <div className="decision-note checking">
        <span>
          {ASK_BY_VIA[decision.unsubscribed_via] ||
            "The sender’s page didn’t say clearly. Did it confirm you’re unsubscribed?"}
        </span>
        <span className="note-actions">
          <button className="btn sm" onClick={() => onAnswer(true)}>Yes, it worked</button>
          <button className="btn sm" onClick={() => onAnswer(false)}>No</button>
        </span>
      </div>
    );
  }
  if (decision.status === "page_failed") {
    return (
      <div className="decision-note failed">
        <span>The sender’s page reported an error, so this didn’t work.</span>
        <span className="note-actions">
          <button className="link inline" onClick={onRetry}>Try another way</button>
        </span>
      </div>
    );
  }
  if (decision.status === "confirmed") {
    return (
      <p className="decision-note done">
        ✓ Unsubscribed. No emails since {shortDate(decision.unsubscribed_at)}.
      </p>
    );
  }
  if (decision.status === "still_sending") {
    return (
      <div className="decision-note failed">
        <span>
          Still emailing you: {decision.emails_since} email{decision.emails_since === 1 ? "" : "s"} since you
          unsubscribed. It may not have gone through.
        </span>
        <span className="note-actions">
          <button className="link inline" onClick={onRetry}>Try another way</button>
          <button className="link inline" onClick={onReset}>Undo</button>
        </span>
      </div>
    );
  }
  return (
    <div className="decision-note checking">
      <span>
        {VIA_LABELS[decision.unsubscribed_via]} Simplispace will confirm it automatically if no new emails arrive by{" "}
        {shortDate(decision.confirm_after)}.
      </span>
      <button className="link inline" onClick={onReset}>Didn’t finish? Undo</button>
    </div>
  );
}

function StillSending({ services, decisions, onRetry }) {
  const failing = services.filter((s) =>
    ["still_sending", "page_failed"].includes(decisions[s.domain]?.status),
  );
  if (failing.length === 0) return null;
  return (
    <section className="panel attention">
      <div className="panel-head">
        <strong>Unsubscribes that didn’t work</strong>
        <span className="muted small">These senders kept emailing you after you unsubscribed.</span>
      </div>
      <ul className="rows">
        {failing.map((s) => (
          <li key={s.domain}>
            <div className="review-name">
              <span className="svc-name">{s.name}</span>
              <span className="muted small">
                {decisions[s.domain].status === "page_failed"
                  ? "The sender’s page reported an error"
                  : `${decisions[s.domain].emails_since} new email${decisions[s.domain].emails_since === 1 ? "" : "s"} since ${shortDate(decisions[s.domain].unsubscribed_at)}`}
              </span>
            </div>
            <button className="btn sm danger-soft" onClick={() => onRetry(s)}>Try another way</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return "the sender";
  }
}

function mailtoAddress(url) {
  return decodeURIComponent(url.replace(/^mailto:/i, "").split("?")[0]);
}

function UnsubscribeDialog({ service, onClose, onDone }) {
  const online = useOnline();
  const [options, setOptions] = useState(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  useEffect(() => {
    api.unsubscribeOptions(service.domain)
      .then(setOptions)
      .catch((e) => setError(String(e.message || e)));
  }, [service.domain]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !working && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, working]);

  async function run(method) {
    setWorking(true);
    setError("");
    try {
      onDone(service.domain, await api.unsubscribe(service.domain, method));
    } catch (e) {
      setError(String(e.message || e));
      setWorking(false);
    }
  }

  const choices = options
    ? [
        options.one_click && {
          method: "one_click",
          title: "Unsubscribe now (one-click)",
          detail: `Simplispace sends a standard unsubscribe request to ${hostOf(options.link)}. Nothing is sent from your Gmail.`,
        },
        options.link && {
          method: "link",
          title: options.one_click ? "Unsubscribe in a Simplispace window" : "Open the unsubscribe page",
          detail: options.one_click
            ? `Opens a Simplispace window that sends the unsubscribe request to ${hostOf(options.link)}. Use this if one-click is refused.`
            : `Opens ${hostOf(options.link)} in a Simplispace window so you can finish there.`,
        },
        options.mailto && {
          method: "mailto",
          title: "Unsubscribe by email",
          detail: `Opens a ready Gmail draft to ${mailtoAddress(options.mailto)} inside Simplispace. Press Send; the app notices when it’s sent.`,
        },
        options.gmail && (options.link || options.mailto) && {
          method: "gmail",
          title: "Use Gmail’s Unsubscribe button",
          detail: "Opens their latest email in Gmail inside Simplispace. Click Unsubscribe next to the sender’s name; the app notices when Gmail confirms it.",
        },
        options.gmail && {
          method: "block",
          title: `Block ${service.name} in Gmail`,
          detail: `Opens their latest email in Gmail inside Simplispace. Click ⋮ (More) at the top right of the email, then Block “${service.name}”. Future emails go straight to Spam.`,
        },
      ].filter(Boolean)
    : [];

  return (
    <div className="modal-backdrop" onClick={() => !working && onClose()}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="unsub-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="unsub-title">Unsubscribe from {service.name}?</h2>
        <p className="muted modal-sub">
          {service.sender_email || service.domain} · {service.email_count.toLocaleString()} emails.
          Pick how to unsubscribe. Nothing happens until you choose one.
        </p>
        {!options && !error && <p className="muted">Checking this sender’s unsubscribe options…</p>}
        {options && !options.link && !options.mailto && (
          <p className="no-unsub">
            {service.name}’s emails don’t include an unsubscribe link, so Gmail shows no Unsubscribe button
            either. Blocking the sender is the way to stop them.
          </p>
        )}
        <div className="unsub-choices">
          {choices.map((choice) => (
            <button
              key={choice.method}
              className="unsub-choice"
              disabled={working || !online}
              onClick={() => run(choice.method)}
            >
              <strong>{choice.title}</strong>
              <span className="muted small">{choice.detail}</span>
            </button>
          ))}
        </div>
        {!online && (
          <p className="error" role="alert">
            You’re offline. Unsubscribing needs the internet; these options come back when you reconnect.
          </p>
        )}
        {error && online && <p className="error">{error}</p>}
        <div className="modal-actions">
          <button className="btn" onClick={onClose} disabled={working} autoFocus>
            {working ? "Working…" : "Cancel"}
          </button>
        </div>
      </div>
    </div>
  );
}

// Unsubscribe from several senders at once. Only senders that support one-click can
// be done in bulk; the rest need their own page, so they're listed to do one by one.
function BulkUnsubscribeDialog({ services, onClose, onDone }) {
  const online = useOnline();
  const [checked, setChecked] = useState(0);
  const [plan, setPlan] = useState(null); // { instant: [...], manual: [...] }
  const [working, setWorking] = useState(false);
  const [done, setDone] = useState(0);
  const [failed, setFailed] = useState([]);
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const instant = [], manual = [];
      for (const service of services) {
        try {
          const options = await api.unsubscribeOptions(service.domain);
          (options.one_click ? instant : manual).push(service);
        } catch {
          manual.push(service);
        }
        if (cancelled) return;
        setChecked((n) => n + 1);
      }
      setPlan({ instant, manual });
    })();
    return () => {
      cancelled = true;
    };
  }, [services]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !working && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, working]);

  async function run() {
    setWorking(true);
    const failures = [];
    for (const service of plan.instant) {
      try {
        onDone(service.domain, await api.unsubscribe(service.domain, "one_click", true));
      } catch (e) {
        failures.push({ service, reason: String(e.message || e) });
      }
      setDone((n) => n + 1);
    }
    setFailed(failures);
    setWorking(false);
    setFinished(true);
  }

  const names = (list) => list.map((s) => s.name).join(", ");

  return (
    <div className="modal-backdrop" onClick={() => !working && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="bulk-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="bulk-title">Unsubscribe from {plural(services.length, "sender")}?</h2>
        {!plan && (
          <p className="muted modal-sub">Checking unsubscribe options… {checked} of {services.length}</p>
        )}
        {plan && !finished && (
          <>
            {plan.instant.length > 0 ? (
              <p className="modal-sub">
                <strong>{plan.instant.length} can be unsubscribed now</strong> with a standard one-click request
                to each sender: {names(plan.instant)}. Nothing is sent from your Gmail.
              </p>
            ) : (
              <p className="modal-sub">None of these senders support one-click unsubscribe.</p>
            )}
            {plan.manual.length > 0 && (
              <p className="muted small">
                {plan.manual.length} need their own page or an email, so do them one by one with their
                Unsubscribe button: {names(plan.manual)}.
              </p>
            )}
          </>
        )}
        {working && <p className="muted">Unsubscribing… {done} of {plan.instant.length}</p>}
        {finished && (
          <>
            <p className="modal-sub">
              Sent {plural(plan.instant.length - failed.length, "unsubscribe request")}. Simplispace will confirm
              once their emails stop.
            </p>
            {failed.length > 0 && (
              <p className="muted small">
                {failed.length} refused the automatic request: {names(failed.map((f) => f.service))}. Use their
                Unsubscribe button to finish in a Simplispace window.
              </p>
            )}
          </>
        )}
        {!online && (
          <p className="error" role="alert">You’re offline. Unsubscribing needs the internet.</p>
        )}
        <div className="modal-actions">
          <button className="btn" onClick={onClose} disabled={working} autoFocus>
            {finished ? "Done" : "Cancel"}
          </button>
          {plan && !finished && plan.instant.length > 0 && (
            <button className="btn danger-solid" onClick={run} disabled={working || !online}>
              {working ? "Working…" : `Unsubscribe from ${plan.instant.length}`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Sparkline({ points }) {
  const w = 180;
  const h = 40;
  const step = w / (points.length - 1);
  const path = points
    .map((p, i) => `${(i * step).toFixed(1)},${(h - 2 - (p / 100) * (h - 4)).toFixed(1)}`)
    .join(" ");
  return (
    <svg
      className="sparkline"
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`Health score history: ${points.join(", ")}`}
    >
      <polyline points={path} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
    </svg>
  );
}

const HEALTH_GRADES = [
  { min: 80, label: "Excellent", tone: "cat-important", line: "Your inbox is in great shape." },
  { min: 60, label: "Good", tone: "cat-useful", line: "Your inbox is mostly mail worth reading." },
  { min: 40, label: "Fair", tone: "cat-optional", line: "A fair amount of your mail is noise." },
  { min: 0, label: "Cluttered", tone: "cat-marketing", line: "Most of your mail is noise." },
];

const MIX_PARTS = [
  { category: "Important", label: "Important", color: "var(--green)" },
  { category: "Useful", label: "Useful", color: "var(--blue)" },
  { category: "Optional", label: "Optional", color: "var(--orange)" },
  { category: "Marketing/Inactive", label: "Marketing", color: "var(--red)" },
];

function formatMinutes(minutes) {
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function HealthGauge({ score, potential, tone }) {
  const r = 54;
  const c = 2 * Math.PI * r;
  const arc = (value) => `${(c * value) / 100} ${c}`;
  return (
    <div className={`gauge ${tone}`}>
      <svg width="140" height="140" viewBox="0 0 140 140" aria-hidden="true">
        <circle cx="70" cy="70" r={r} className="gauge-track" />
        {potential > score && (
          <circle cx="70" cy="70" r={r} className="gauge-ghost" strokeDasharray={arc(potential)} />
        )}
        <circle cx="70" cy="70" r={r} className="gauge-fill" strokeDasharray={arc(score)} />
      </svg>
      <div className="gauge-center">
        <span className="gauge-num">{score}</span>
        <span className="gauge-of">/100</span>
      </div>
    </div>
  );
}

function HealthPanel({ insights, onImprove }) {
  if (!insights || insights.health_score == null) return null;
  const score = insights.health_score;
  const potential = insights.potential_health_score;
  const grade = HEALTH_GRADES.find((g) => score >= g.min);
  const history = insights.history;
  const change = history.length > 1 ? score - history[0].score : 0;
  const worthReading = (insights.mix.Important || 0) + (insights.mix.Useful || 0);
  const gain = potential - score;
  const since = history.length > 1
    ? new Date(history[0].day).toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : "";
  return (
    <section className="panel health">
      <div className="health-score">
        <HealthGauge score={score} potential={potential} tone={grade.tone} />
        <span className={`badge ${grade.tone}`}>{grade.label}</span>
        {change !== 0 && (
          <span className={`trend ${change > 0 ? "up" : "down"}`}>
            {change > 0 ? "▲" : "▼"} {Math.abs(change)} since {since}
          </span>
        )}
      </div>

      <div className="health-body">
        <span className="stat-label">Inbox health</span>
        <h2 className="health-line">{grade.line}</h2>
        <p className="muted health-sub">
          {worthReading}% of the mail you got in the last {insights.window_days} days comes from senders worth
          reading.
        </p>

        <div
          className="mix-bar"
          role="img"
          aria-label={MIX_PARTS.map((p) => `${p.label} ${insights.mix[p.category] || 0}%`).join(", ")}
        >
          {MIX_PARTS.map((p) =>
            insights.mix[p.category] ? (
              <span key={p.category} style={{ width: `${insights.mix[p.category]}%`, background: p.color }} />
            ) : null,
          )}
        </div>
        <div className="mix-legend">
          {MIX_PARTS.map((p) => (
            <span key={p.category}>
              <i style={{ background: p.color }} /> {p.label} {insights.mix[p.category] || 0}%
            </span>
          ))}
        </div>

        {gain > 0 && insights.candidate_count > 0 ? (
          <div className="goal">
            <div>
              <strong>Reach {potential}/100</strong>
              <span className="muted small">
                Unsubscribe from {insights.candidate_count} marketing sender
                {insights.candidate_count === 1 ? "" : "s"} for +{gain} points and{" "}
                {insights.potential_monthly_reduction} fewer emails a month.
              </span>
            </div>
            <button className="btn primary sm" onClick={onImprove}>Start cleaning up</button>
          </div>
        ) : (
          <div className="goal done">
            <strong>Nothing left to clean up</strong>
            <span className="muted small">Every marketing sender is handled. Sync later to check for new ones.</span>
          </div>
        )}
      </div>

      <div className="health-stats">
        <div className="health-stat">
          <span className="stat-label">Emails cut / month</span>
          <span className="health-val">{insights.monthly_reduction}</span>
          <span className="muted small">
            up to {insights.monthly_reduction + insights.potential_monthly_reduction} possible
          </span>
        </div>
        <div className="health-stat">
          <span className="stat-label">Time saved / year</span>
          <span className="health-val">{formatMinutes(insights.minutes_saved_per_year)}</span>
          <span className="muted small">
            up to {formatMinutes(insights.minutes_saved_per_year + insights.potential_minutes_saved_per_year)} possible
          </span>
        </div>
        <div className="health-stat">
          <span className="stat-label">Unsubscribed</span>
          <span className="health-val">{insights.unsubscribed_count}</span>
          <span className="muted small">sender{insights.unsubscribed_count === 1 ? "" : "s"} so far</span>
        </div>
        {history.length > 1 && (
          <div className="health-stat">
            <span className="stat-label">Last {history.length} days</span>
            <Sparkline points={history.map((p) => p.score)} />
          </div>
        )}
      </div>

      <details className="health-how">
        <summary>How is this calculated?</summary>
        <p className="muted small">
          Health is the share of your last {insights.window_days} days of mail that comes from Important or Useful
          senders (Optional counts half, Marketing counts zero). Senders you unsubscribed from are left out, so the
          score rises as you clean up. Time saved assumes {insights.seconds_per_email} seconds per unwanted email.
        </p>
      </details>
    </section>
  );
}

function plural(n, word) {
  return `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
}

function ago(days) {
  if (days == null) return "at an unknown date";
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 60) return `${days} days ago`;
  if (days < 730) return `about ${Math.round(days / 30)} months ago`;
  return `about ${Math.round(days / 365)} years ago`;
}

// Builds a sender-specific explanation from the evidence the backend returns.
function explain(service) {
  const f = service.facts || {};
  const n = service.email_count;
  const pct = Math.round(service.open_rate * 100);
  const fired = new Set(service.signals.map((s) => s.key));
  const name = service.name;

  const single = n === 1;
  // A single "order" subject among promos shouldn't make a marketing list look transactional.
  const mostlyTransactional = fired.has("transactional") && (f.transactional_count || 0) / n >= 0.3;
  const marketingList = fired.has("promotional") && fired.has("has_unsubscribe");

  let headline;
  if (fired.has("replied") && fired.has("transactional")) headline = `You deal with ${name} directly, and they send things you act on.`;
  else if (fired.has("replied")) headline = `You’ve written to ${name} yourself, so this is a real conversation, not a mailing list.`;
  else if (fired.has("inactive_bulk")) headline = `${name} sends a lot and you almost never open it.`;
  else if (marketingList && !mostlyTransactional)
    headline = fired.has("transactional")
      ? `${name} is mostly marketing, with the odd order or account email mixed in.`
      : `${name} looks like a marketing list.`;
  else if (fired.has("transactional") && fired.has("opened_frequently")) headline = `${name} sends things you act on, and you read them.`;
  else if (fired.has("transactional"))
    headline = single
      ? `${name} sent you a booking, order, or account email.`
      : `${name} sends account or order mail, even if you rarely open it.`;
  else if (fired.has("opened_frequently")) headline = `You read ${name} regularly.`;
  else if (fired.has("promotional")) headline = `${name} is mostly promotional mail${pct < 10 ? " you don’t open" : ""}.`;
  else if (fired.has("has_unsubscribe")) headline = `${name} is a newsletter-style sender you rarely engage with.`;
  else headline = `${name} sends mail occasionally, with no strong signs either way.`;

  const rate =
    !f.per_month || single ? "" : f.per_month >= 1 ? `, about ${f.per_month} a month` : ", less than one a month";
  const story = (
    single
      ? [
          `Just one email, received ${ago(service.days_since_last)}.`,
          pct === 0 ? "You haven’t opened it." : "You opened it.",
        ]
      : [
          `${plural(n, "email")}${f.first_days_ago != null ? ` since ${ago(f.first_days_ago)}` : ""}${rate}.`,
          pct === 0 ? "You haven’t opened any of them." : `You opened ${f.read_count} of them (${pct}%).`,
          `The latest arrived ${ago(service.days_since_last)}.`,
        ]
  ).join(" ");

  const words = (f.transactional_words || []).map((w) => `“${w}”`).join(", ");
  const evidence = {
    promotional: single ? "Gmail filed it under Promotions." : `Gmail filed ${f.promo_count} of ${n} under Promotions.`,
    has_unsubscribe: single
      ? "It includes an unsubscribe link, typical of mailing lists."
      : `${f.unsub_count} of ${n} include an unsubscribe link, typical of mailing lists.`,
    replied: `You’ve sent email to ${service.domain}.`,
    transactional: single
      ? `The subject mentions ${words}.`
      : `${f.transactional_count} of ${n} subjects mention ${words}.`,
    opened_frequently: single ? "You opened it." : `You opened ${f.read_count} of ${n}, at least half.`,
    inactive_bulk: `${n} emails and only ${f.read_count} opened.`,
  };

  const notes = [];
  if (!fired.has("opened_frequently") && pct >= 30) notes.push(`You open ${pct}%, close to the 50% that would add +25.`);
  if (!fired.has("promotional") && f.promo_count > 0) notes.push(`${f.promo_count} of ${n} were in Promotions, under the half needed to count against it.`);
  if (!fired.has("inactive_bulk") && n >= 30 && pct < 10 && !f.replied)
    notes.push(`${plural(50 - n, "more unopened email")} would cost it another 40 points.`);

  let advice;
  if (service.category === "Important") advice = `Keep ${name}. It’s one of the senders that matters most in your inbox.`;
  else if (service.category === "Useful") advice = `Probably keep it. ${pct < 20 ? "You rarely open it, so skim a few before deciding." : "You do read it."}`;
  else if (service.category === "Optional")
    advice =
      pct < 10
        ? `You barely open ${name}. If nothing in the recent subjects below stands out, unsubscribing is safe.`
        : `You open some of these. Check the recent subjects below and decide if you’d miss them.`;
  else
    advice =
      f.per_month >= 1
        ? `A good candidate to unsubscribe. You’d get about ${f.per_month} fewer emails a month.`
        : "A good candidate to unsubscribe, though it rarely emails you, so it’s low impact either way.";

  const whatIf = (f.what_if || [])
    .filter((w) => w.category !== service.category)
    .map((w) =>
      w.action === "open"
        ? `If you started opening these, it would score ${w.score} (${w.category}).`
        : `If you replied to ${name}, it would score ${w.score} (${w.category}).`,
    );
  if (f.next_category && f.points_to_next) {
    whatIf.unshift(`${f.points_to_next} more point${f.points_to_next === 1 ? "" : "s"} would move it up to ${f.next_category}.`);
  }

  return { headline, story, evidence, notes, advice, whatIf, subjects: f.recent_subjects || [] };
}

function KeepDialog({ service, onCancel, onConfirm }) {
  const [working, setWorking] = useState(false);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !working && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, working]);
  return (
    <div className="modal-backdrop" onClick={() => !working && onCancel()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="keep-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="keep-title">Keep {service.name}?</h2>
        <p className="muted modal-sub">
          {service.sender_email || service.domain} will be marked as wanted and won’t be shown in your senders
          again. You can undo this anytime from History.
        </p>
        <div className="modal-actions gap">
          <button className="btn" onClick={onCancel} disabled={working}>Cancel</button>
          <button
            className="btn primary"
            autoFocus
            disabled={working}
            onClick={async () => {
              setWorking(true);
              await onConfirm();
            }}
          >
            {working ? "Saving…" : "Keep sender"}
          </button>
        </div>
      </div>
    </div>
  );
}

// Log out keeps everything on this computer, so it's a light confirmation, not a warning.
function LogoutDialog({ email, onCancel, onConfirm }) {
  const [working, setWorking] = useState(false);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !working && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, working]);
  return (
    <div className="modal-backdrop" onClick={() => !working && onCancel()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="logout-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="logout-title">Log out?</h2>
        <p className="muted modal-sub">
          You’ll be signed out of <strong>{email}</strong>. Your senders, choices and history stay on this
          computer and are back as soon as you sign in again.
        </p>
        <div className="modal-actions gap">
          <button className="btn" onClick={onCancel} disabled={working}>Cancel</button>
          <button
            className="btn primary"
            autoFocus
            disabled={working}
            onClick={async () => {
              setWorking(true);
              await onConfirm();
            }}
          >
            {working ? "Logging out…" : "Log out"}
          </button>
        </div>
      </div>
    </div>
  );
}

// Standard "destructive action" confirmation: says exactly what goes and what stays,
// Cancel is the default (focused, Esc, clicking outside), and the red button names the action.
function DeleteAccountDialog({ email, senders, choices, onCancel, onConfirm }) {
  const online = useOnline();
  const [working, setWorking] = useState(false);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !working && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, working]);
  return (
    <div className="modal-backdrop" onClick={() => !working && onCancel()}>
      <div
        className="modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-title"
        aria-describedby="delete-desc"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="warn-icon" aria-hidden="true">!</div>
        <h2 id="delete-title">Delete your Simplispace account?</h2>
        <p id="delete-desc" className="muted modal-sub">
          This permanently deletes everything Simplispace stored for <strong>{email}</strong>. It can’t be undone.
          To just sign out and keep your data, use Log out instead.
        </p>
        <ul className="logout-list">
          <li className="gone">
            <strong>Deleted from this computer:</strong> {senders.toLocaleString()} scored sender
            {senders === 1 ? "" : "s"}, {choices.toLocaleString()} keep/unsubscribe choice{choices === 1 ? "" : "s"},
            and your health history.
          </li>
          <li className="gone"><strong>Gmail access removed:</strong> Simplispace can no longer read your mail.</li>
          <li className="kept"><strong>Not changed:</strong> your Gmail, its emails, and any unsubscribes already sent.</li>
        </ul>
        {!online && (
          <p className="error small">
            You’re offline, so Google can’t be told right now. Your data is still deleted here; to remove access
            fully, visit myaccount.google.com/permissions later.
          </p>
        )}
        <div className="modal-actions gap">
          <button className="btn" onClick={onCancel} disabled={working} autoFocus>Cancel</button>
          <button
            className="btn danger-solid"
            disabled={working}
            onClick={async () => {
              setWorking(true);
              await onConfirm();
            }}
          >
            {working ? "Deleting…" : "Delete account"}
          </button>
        </div>
      </div>
    </div>
  );
}

// Only re-render a card when its own data changes, not on every sync progress tick.
// Handlers are skipped in the check: they only call state setters and the API.
const ServiceCard = memo(ServiceCardInner, (a, b) =>
  a.service === b.service && a.decision === b.decision && a.showLogos === b.showLogos &&
  a.selected === b.selected);

// Arrow keys move between cards in the grid (row length read from the layout).
function moveCardFocus(card, key) {
  const cards = [...card.parentElement.querySelectorAll(".service-card")];
  const i = cards.indexOf(card);
  const perRow = Math.max(1, cards.filter((c) => c.offsetTop === cards[0].offsetTop).length);
  const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: perRow, ArrowUp: -perRow }[key];
  cards[i + step]?.focus();
}

// The sender's newest emails, loaded when the preview is opened.
function RecentEmails({ domain }) {
  const [emails, setEmails] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.senderEmails(domain).then(setEmails).catch((e) => setError(String(e.message || e)));
  }, [domain]);
  if (error) return <p className="error small">{error}</p>;
  if (!emails) return <p className="muted small">Loading emails…</p>;
  if (emails.length === 0) return <p className="muted small">No emails stored for this sender.</p>;
  return (
    <ul className="recent-emails">
      {emails.map((email, i) => (
        <li key={i} className={email.unread ? "unread" : ""}>
          <span className="recent-subject" title={email.subject}>{email.subject}</span>
          <span className="muted small">{email.date ? shortDate(email.date) : ""}</span>
        </li>
      ))}
    </ul>
  );
}

function ServiceCardInner({
  service, decision, onUnsubscribe, onKeep, onReset, onRetry, onAnswer, showLogos, selected, onSelect,
}) {
  const unsubscribed = Boolean(decision?.unsubscribed_at);
  const categoryClass = CATEGORY_CLASS[service.category] || "";
  const initial = (service.name || service.domain || "?").charAt(0).toUpperCase();
  const [logoSource, setLogoSource] = useState(0);
  const [logoLoaded, setLogoLoaded] = useState(false);
  const [showWhy, setShowWhy] = useState(false);
  const [showEmails, setShowEmails] = useState(false);

  // Shortcuts while the card itself has focus (not a button inside it).
  function onKeyDown(e) {
    if (e.target !== e.currentTarget || e.ctrlKey || e.metaKey || e.altKey) return;
    const key = e.key.toLowerCase();
    if (e.key.startsWith("Arrow")) moveCardFocus(e.currentTarget, e.key);
    else if (key === "k" && !unsubscribed) onKeep(service);
    else if (key === "u" && !unsubscribed) onUnsubscribe(service);
    else if ((key === "x" || key === " ") && onSelect && !unsubscribed) onSelect(service.domain);
    else if (key === "e") setShowEmails((v) => !v);
    else return;
    e.preventDefault();
  }
  // Served (and cached on disk) by the backend; missing ones fall back to the initial.
  const logoSources = showLogos ? [`${api.base}/icon/${encodeURIComponent(service.domain)}`] : [];
  const logoUrl = logoSources[logoSource];
  const why = explain(service);
  const positivePoints = service.signals
    .filter((signal) => signal.points > 0)
    .reduce((total, signal) => total + signal.points, 0);
  const negativePoints = service.signals
    .filter((signal) => signal.points < 0)
    .reduce((total, signal) => total + signal.points, 0);
  return (
    <article
      className={`service-card ${selected ? "selected" : ""}`}
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label={`${service.name}, ${service.category}, score ${service.score}`}
    >
      <div className="service-card-head">
        <div className="service-card-title">
          {onSelect && !unsubscribed && (
            <input
              type="checkbox"
              className="card-select"
              checked={Boolean(selected)}
              onChange={() => onSelect(service.domain)}
              aria-label={`Select ${service.name}`}
            />
          )}
          <span className={`service-icon ${categoryClass}`} aria-hidden="true">
            {(!logoUrl || !logoLoaded) && (
              <span className="logo-fallback">{initial}</span>
            )}
            {logoUrl && (
              <img
                className="service-logo"
                key={logoUrl}
                src={logoUrl}
                alt=""
                loading="lazy"
                decoding="async"
                referrerPolicy="no-referrer"
                onLoad={() => setLogoLoaded(true)}
                onError={() =>
                  {
                    setLogoLoaded(false);
                    setLogoSource((source) => source + 1);
                  }
                }
              />
            )}
          </span>
          <div className="svc-info">
            <div className="svc-name">{service.name}</div>
            <div className="muted small sender-email" title={service.sender_email || service.domain}>
              {service.sender_email || service.domain}
            </div>
          </div>
        </div>
        <div className="score-wrap">
          <span className="score-caption">SCORE</span>
          <span className={`score-chip ${categoryClass}`}>{service.score}</span>
        </div>
      </div>

      <div className="service-category-row">
        <span className={`badge ${categoryClass}`}>{service.category}</span>
      </div>

      <div className="service-stats-grid">
        <div className="stat-cell">
          <span className="stat-label">Emails</span>
          <span className="stat-val">{service.email_count.toLocaleString()}</span>
        </div>
        <div className="stat-divider" />
        <div className="stat-cell">
          <span className="stat-label">Opened</span>
          <span className="stat-val">{Math.round(service.open_rate * 100)}%</span>
        </div>
        <div className="stat-divider" />
        <div className="stat-cell">
          <span className="stat-label">Last email</span>
          <span className="stat-val">
            {service.days_since_last == null ? "Never" : service.days_since_last === 0 ? "Today" : `${service.days_since_last}d ago`}
          </span>
        </div>
      </div>

      <button
        className="show-why-btn"
        type="button"
        onClick={() => setShowWhy((visible) => !visible)}
        aria-expanded={showWhy}
      >
        <span>{showWhy ? "Hide explanation" : "Show why"}</span>
        <span
          className={`show-why-chevron ${showWhy ? "open" : ""}`}
          aria-hidden="true"
        />
      </button>

      {showWhy && (
        <div className="service-card-why">
          <p className="why-headline">{why.headline}</p>
          <p className="why-summary">{why.story}</p>

          <span className="why-insight-label">What decided the score</span>
          {service.signals.length > 0 ? (
            <ul className="why-evidence">
              {service.signals.map((signal) => (
                <li key={signal.key}>
                  <span>
                    <b>{signal.label}</b>
                    <span className="why-detail">{why.evidence[signal.key]}</span>
                  </span>
                  <span className={signal.points >= 0 ? "pos" : "neg"}>
                    {signal.points >= 0 ? "+" : ""}{signal.points}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="why-summary">No signal applied, so it stays at the neutral starting score of 50.</p>
          )}
          <div className="why-breakdown">
            <span>Start 50</span>
            <span className="pos">+{positivePoints}</span>
            <span className="neg">{negativePoints}</span>
            <strong>= {service.score}/100 · {service.category}</strong>
          </div>
          {why.notes.length > 0 && (
            <ul className="why-notes">
              {why.notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          )}

          {why.subjects.length > 0 && (
            <>
              <span className="why-insight-label">Recent subjects</span>
              <ul className="why-subjects">
                {why.subjects.map((subject) => <li key={subject}>“{subject}”</li>)}
              </ul>
            </>
          )}

          <div className="why-advice">
            <span className="why-insight-label">Our take</span>
            <span>{why.advice}</span>
            {why.whatIf.map((line) => <span key={line} className="muted">{line}</span>)}
          </div>
        </div>
      )}

      <button
        className="show-why-btn recent-btn"
        type="button"
        onClick={() => setShowEmails((visible) => !visible)}
        aria-expanded={showEmails}
      >
        <span>{showEmails ? "Hide recent emails" : "Recent emails"}</span>
        <span className={`show-why-chevron ${showEmails ? "open" : ""}`} aria-hidden="true" />
      </button>
      {showEmails && <RecentEmails domain={service.domain} />}

      {unsubscribed ? (
        <UnsubscribeStatus
          decision={decision}
          onRetry={() => onRetry(service)}
          onReset={() => onReset(service.domain)}
          onAnswer={(worked) => onAnswer(service, worked)}
        />
      ) : (
        <div className="service-card-actions">
          <button className="decision-btn keep" onClick={() => onKeep(service)}>
            Keep
          </button>
          <button className="decision-btn unsubscribe" onClick={() => onUnsubscribe(service)}>
            Unsubscribe
          </button>
        </div>
      )}
    </article>
  );
}

const ICON_PATHS = {
  wifiOff: "M2 2l20 20M8.5 16.5a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 5.2-2.8M19 12.9a10 10 0 0 0-2.4-1.7M2 8.8a15 15 0 0 1 4.2-2.7M10.7 5.1A15 15 0 0 1 22 8.8M12 20h.01",
  overview: "M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  senders: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  history: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  profile: "M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10",
  privacy: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  settings: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  logout: "M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H3",
  sync: "M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6",
};

function OfflineBanner() {
  const [checking, setChecking] = useState(false);
  async function retry() {
    setChecking(true);
    await checkOnline();
    setChecking(false);
  }
  return (
    <div className="offline-banner" role="alert">
      <Icon name="wifiOff" />
      <div>
        <strong>You’re offline</strong>
        <span className="small">
          Showing your saved senders. Keep and Undo still work; syncing and unsubscribing resume when you’re back online.
        </span>
      </div>
      <button className="btn sm" onClick={retry} disabled={checking}>
        {checking ? "Checking…" : "Try again"}
      </button>
    </div>
  );
}

function Icon({ name, className = "" }) {
  return (
    <svg className={`icon ${className}`} width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

const NAV = [
  { section: "Inbox", items: [
    { id: "overview", label: "Overview" },
    { id: "senders", label: "All senders" },
    { id: "history", label: "History" },
  ] },
  { section: "Account", items: [
    { id: "profile", label: "Profile" },
    { id: "privacy", label: "Privacy & data" },
    { id: "settings", label: "Settings" },
  ] },
];

const VIEW_TITLES = {
  overview: ["Overview", "How healthy your inbox is and what needs your attention."],
  senders: ["All senders", "Every service that emails you, scored and categorized."],
  history: ["History", "Senders you kept or unsubscribed from."],
  profile: ["Profile", "Your connected Gmail account."],
  privacy: ["Privacy & data", "What Simplispace reads, stores, and never does."],
  settings: ["Settings", "Theme and zoom for this app."],
};

function Sidebar({ view, onNavigate, status, busy, reading, onLogout, loggingOut }) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem("sidebarCollapsed") === "1";
    } catch {
      return false;
    }
  });

  function toggle() {
    setCollapsed((was) => {
      try {
        localStorage.setItem("sidebarCollapsed", was ? "0" : "1");
      } catch {
        /* preference just isn't remembered */
      }
      return !was;
    });
  }

  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <button
        className="brand"
        onClick={toggle}
        aria-label={collapsed ? "Simplispace, expand menu" : "Simplispace, collapse menu"}
        aria-expanded={!collapsed}
        title={collapsed ? "Expand menu" : "Collapse menu"}
      >
        <span className="logo-badge sm">
          <img src="/logo-mark.png" alt="" />
        </span>
        <span className="brand-name">Simplispace</span>
      </button>

      <nav className="nav" aria-label="Main">
        {NAV.map((group) => (
          <div className="nav-group" key={group.section}>
            <span className="nav-section">{group.section}</span>
            {group.items.map((item) => (
              <button
                key={item.id}
                className={`nav-item ${view === item.id ? "active" : ""}`}
                title={collapsed ? item.label : undefined}
                onClick={() => onNavigate(item.id)}
                aria-current={view === item.id ? "page" : undefined}
              >
                <Icon name={item.id} />
                <span className="nav-label">{item.label}</span>
              </button>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        <button className="user-card" onClick={() => onNavigate("profile")} title={collapsed ? status.email : undefined}>
          <span className="avatar sm" aria-hidden="true">{status.email.charAt(0).toUpperCase()}</span>
          <span className="user-text">
            <span className="user-email">{status.email}</span>
            <span className="user-status">
              <span className={`status-dot ${busy ? "busy" : ""}`} aria-hidden="true" />
              {reading !== null
                ? `Reading inbox… ${reading.toLocaleString()} emails`
                : busy ? "Syncing…" : status.last_synced_at ? `Synced ${timeAgo(status.last_synced_at)}` : "Not synced yet"}
            </span>
          </span>
        </button>
        <button className="nav-item logout" onClick={onLogout} disabled={loggingOut} title={collapsed ? "Log out" : undefined}>
          <Icon name="logout" />
          <span className="nav-label">{loggingOut ? "Logging out…" : "Log out"}</span>
        </button>
      </div>
    </aside>
  );
}

const CATEGORY_TILES = [
  { category: "Important", hint: "Keep these" },
  { category: "Useful", hint: "Probably keep" },
  { category: "Optional", hint: "Worth a look" },
  { category: "Marketing/Inactive", hint: "Unsubscribe candidates" },
];

function CategoryTiles({ services, onOpen }) {
  return (
    <div className="tiles">
      {CATEGORY_TILES.map(({ category, hint }) => {
        const list = services.filter((s) => s.category === category);
        const emails = list.reduce((sum, s) => sum + s.email_count, 0);
        return (
          <button key={category} className={`tile ${CATEGORY_CLASS[category]}`} onClick={() => onOpen(category)}>
            <span className="tile-label">{category}</span>
            <span className="tile-num">{list.length}</span>
            <span className="tile-sub">{emails.toLocaleString()} emails · {hint}</span>
          </button>
        );
      })}
    </div>
  );
}

function Candidates({ services, decisions, onUnsubscribe, onSeeAll }) {
  const top = services
    .filter((s) => s.category === "Marketing/Inactive" && !decisions[s.domain])
    .sort((a, b) => b.email_count - a.email_count)
    .slice(0, 6);
  if (top.length === 0) return null;
  return (
    <section className="panel">
      <div className="panel-head">
        <strong>Top unsubscribe candidates</strong>
        <button className="link" onClick={onSeeAll}>See all</button>
      </div>
      <ul className="rows">
        {top.map((s) => (
          <li key={s.domain}>
            <div className="review-name">
              <span className="svc-name">{s.name}</span>
              <span className="muted small">
                {s.email_count.toLocaleString()} emails · {Math.round(s.open_rate * 100)}% opened
              </span>
            </div>
            <button className="btn sm danger-soft" onClick={() => onUnsubscribe(s)}>Unsubscribe</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function HistoryView({ services, decisions, onUndoKeep, onReset }) {
  const byDomain = Object.fromEntries(services.map((s) => [s.domain, s]));
  const recorded = Object.entries(decisions)
    .filter(([, d]) => d.unsubscribed_at)
    .sort(([, a], [, b]) => b.unsubscribed_at.localeCompare(a.unsubscribed_at));
  const unsubscribed = recorded.filter(([, d]) => CONFIRMED.includes(d.status));
  const waiting = recorded.filter(([, d]) => !CONFIRMED.includes(d.status));
  const kept = Object.entries(decisions).filter(([, d]) => d.decision === "keep");
  const name = (domain) => byDomain[domain]?.name || domain;
  const [unsubShown, unsubMore] = useShowMore(unsubscribed);
  const [keptShown, keptMore] = useShowMore(kept);
  return (
    <>
      {waiting.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <strong>Waiting for confirmation</strong>
            <span className="muted small">Finish these on their cards in All senders.</span>
          </div>
          <ul className="rows">
            {waiting.map(([domain, d]) => (
              <li key={domain}>
                <div className="review-name">
                  <span className="svc-name">{name(domain)}</span>
                  <span className={`status-pill ${d.status}`}>{STATUS_SHORT[d.status]}</span>
                </div>
                <div className="review-actions">
                  <span className="muted small">{new Date(d.unsubscribed_at).toLocaleDateString()}</span>
                  <button className="link" onClick={() => onReset(domain)}>Undo</button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="panel">
        <div className="panel-head">
          <strong>Unsubscribed</strong>
          <span className="muted small">{unsubscribed.length}</span>
        </div>
        {unsubscribed.length === 0 ? (
          <p className="muted small empty">Nothing yet. Confirmed unsubscribes show up here.</p>
        ) : (
          <ul className="rows">
            {unsubShown.map(([domain, d]) => (
              <li key={domain}>
                <div className="review-name">
                  <span className="svc-name">{name(domain)}</span>
                  <span className={`status-pill ${d.status}`}>{STATUS_SHORT[d.status]}</span>
                </div>
                <div className="review-actions">
                  <span className="muted small">{new Date(d.unsubscribed_at).toLocaleDateString()}</span>
                  <button className="link" onClick={() => onReset(domain)}>Undo</button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {unsubMore}
      </section>
      <section className="panel">
        <div className="panel-head">
          <strong>Kept</strong>
          <span className="muted small">{kept.length}</span>
        </div>
        {kept.length === 0 ? (
          <p className="muted small empty">Senders you mark as Keep show up here.</p>
        ) : (
          <ul className="rows">
            {keptShown.map(([domain]) => (
              <li key={domain}>
                <div className="review-name">
                  <span className="svc-name">{name(domain)}</span>
                  <span className="muted small">{byDomain[domain]?.sender_email || domain}</span>
                </div>
                <button className="link" onClick={() => onUndoKeep(domain)}>Undo</button>
              </li>
            ))}
          </ul>
        )}
        {keptMore}
      </section>
    </>
  );
}

// Long History lists show 20 rows at a time.
function useShowMore(items, step = 20) {
  const [limit, setLimit] = useState(step);
  const more = items.length > limit && (
    <button className="link show-more" onClick={() => setLimit(limit + step)}>
      Show {Math.min(step, items.length - limit)} more of {items.length - limit}
    </button>
  );
  return [items.slice(0, limit), more];
}

function ProfileView({ status, services, busy, onSync, onLogout, loggingOut }) {
  const emails = services.reduce((sum, s) => sum + s.email_count, 0);
  return (
    <>
      <section className="panel profile">
        <span className="avatar lg" aria-hidden="true">{status.email.charAt(0).toUpperCase()}</span>
        <div className="profile-text">
          <strong className="profile-email">{status.email}</strong>
          <span className="account-sync">
            <span className={`status-dot ${busy ? "busy" : ""}`} aria-hidden="true" />
            {status.needs_reconnect ? "Reconnect needed to keep syncing" : "Gmail connected · read-only access"}
          </span>
        </div>
      </section>
      <section className="panel">
        <dl className="facts">
          <div><dt>Last synced</dt><dd>{status.last_synced_at ? new Date(status.last_synced_at).toLocaleString() : "Never"}</dd></div>
          <div><dt>Senders found</dt><dd>{services.length.toLocaleString()}</dd></div>
          <div><dt>Emails analysed</dt><dd>{emails.toLocaleString()}</dd></div>
        </dl>
        <div className="profile-actions">
          <button className="btn primary sync-btn" onClick={onSync} disabled={busy}>
            <Icon name="sync" className={busy ? "spin" : ""} />
            {busy ? "Syncing…" : "Sync now"}
          </button>
          <button className="btn danger" onClick={onLogout} disabled={loggingOut}>
            {loggingOut ? "Logging out…" : "Log out"}
          </button>
        </div>
        <p className="muted small">
          Logging out removes Simplispace’s Gmail access but keeps your senders and analysis on this
          computer, ready for when you sign back in. To erase them, use Delete account in Settings.
        </p>
      </section>
    </>
  );
}

function PrivacyView() {
  return (
    <>
      <section className="panel">
        <ul className="notice-points">
          <li><Check /><span>Read-only Gmail access. Simplispace never sends, deletes, or changes your mail.</span></li>
          <li><Check /><span>Only email details are read: sender, subject, date, labels, and unsubscribe headers. Email bodies are never downloaded.</span></li>
          <li><Check /><span>Everything stays on this computer, in a local database. Your Gmail sign-in is stored encrypted.</span></li>
          <li><Check /><span>Sender logos are looked up by website address only, once per sender, via Google’s icon service or the sender’s own site. You can turn this off in Settings.</span></li>
          <li><Check /><span>Nothing is unsubscribed until you confirm how you want to do it.</span></li>
        </ul>
      </section>
      <section className="panel">
        <div className="panel-head"><strong>Manage access</strong></div>
        <p className="muted small">
          You can remove Simplispace’s access at any time from your Google account, or log out here to revoke
          it and delete local data.
        </p>
        <a className="btn" href="https://myaccount.google.com/permissions" target="_blank" rel="noreferrer">
          Open Google account permissions
        </a>
      </section>
    </>
  );
}

const ZOOM_STEPS = [0.8, 0.9, 1, 1.1, 1.25, 1.5];
const THEMES = [["system", "System"], ["light", "Light"], ["dark", "Dark"]];

function readPref(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function writePref(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* preference just isn't remembered */
  }
}

// Theme (system / light / dark) and zoom, applied to the whole page and remembered.
function useDisplaySettings() {
  const [theme, setTheme] = useState(() => readPref("theme", "system"));
  const [logos, setLogosState] = useState(() => readPref("logos", "on") === "on");
  const setLogos = (on) => {
    setLogosState(on);
    writePref("logos", on ? "on" : "off");
  };
  const [zoom, setZoom] = useState(() => {
    const saved = Number(readPref("zoom", "1"));
    return ZOOM_STEPS.includes(saved) ? saved : 1;
  });

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      document.documentElement.dataset.theme = theme === "system" ? (media.matches ? "dark" : "light") : theme;
    };
    apply();
    writePref("theme", theme);
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);

  useEffect(() => {
    document.documentElement.style.zoom = zoom;
    document.documentElement.style.setProperty("--zoom", zoom);
    writePref("zoom", String(zoom));
  }, [zoom]);

  const step = useCallback((direction) => {
    setZoom((current) => {
      const i = ZOOM_STEPS.indexOf(current);
      return ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, Math.max(0, (i === -1 ? 2 : i) + direction))];
    });
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === "=" || e.key === "+") step(1);
      else if (e.key === "-") step(-1);
      else if (e.key === "0") setZoom(1);
      else return;
      e.preventDefault();
    };
    const onWheel = (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      step(e.deltaY < 0 ? 1 : -1);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("wheel", onWheel);
    };
  }, [step]);

  return { theme, setTheme, zoom, setZoom, step, logos, setLogos };
}

function SettingsView({ display, email, onDeleteAccount }) {
  return (
    <>
      <section className="panel">
        <div className="panel-head"><strong>Theme</strong></div>
        <div className="segmented" role="radiogroup" aria-label="Theme">
          {THEMES.map(([value, label]) => (
            <button
              key={value}
              role="radio"
              aria-checked={display.theme === value}
              className={display.theme === value ? "on" : ""}
              onClick={() => display.setTheme(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="muted small">System follows your Windows light or dark setting.</p>
      </section>
      <section className="panel">
        <div className="panel-head"><strong>Sender logos</strong></div>
        <label className="toggle-row">
          <input type="checkbox" checked={display.logos} onChange={(e) => display.setLogos(e.target.checked)} />
          <span>Show sender logos</span>
        </label>
        <p className="muted small">
          Logos are looked up once per sender by its website address (never anything from your emails) and
          saved on this computer. Turn this off to show letters only.
        </p>
      </section>
      <section className="panel">
        <div className="panel-head"><strong>Zoom</strong></div>
        <div className="zoom-row">
          <button className="btn sm" onClick={() => display.step(-1)} disabled={display.zoom <= ZOOM_STEPS[0]} aria-label="Zoom out">−</button>
          <span className="zoom-val">{Math.round(display.zoom * 100)}%</span>
          <button className="btn sm" onClick={() => display.step(1)} disabled={display.zoom >= ZOOM_STEPS[ZOOM_STEPS.length - 1]} aria-label="Zoom in">+</button>
          <button className="link" onClick={() => display.setZoom(1)} disabled={display.zoom === 1}>Reset</button>
        </div>
        <p className="muted small">
          Shortcuts: Ctrl and + to zoom in, Ctrl and − to zoom out, Ctrl and 0 to reset, or Ctrl with the mouse wheel.
        </p>
      </section>

      <section className="panel danger-zone">
        <div className="panel-head"><strong>Delete account</strong></div>
        <p className="muted small">
          Ends Simplispace’s access to {email} and permanently deletes your senders, choices and health history from
          this computer. Your Gmail isn’t changed. To just sign out and keep your data, use Log out.
        </p>
        <button className="btn danger-solid" onClick={onDeleteAccount}>Delete account</button>
      </section>
    </>
  );
}

// In the desktop app, links that would open a browser tab open in a Simplispace
// window instead, so nothing ever needs the external browser.
function useInAppLinks() {
  useEffect(() => {
    const onClick = (e) => {
      const link = e.target.closest?.('a[target="_blank"]');
      if (!link || !link.href.startsWith("https://")) return;
      e.preventDefault();
      const fallback = () => window.open(link.href, "_blank", "noopener");
      api.openInApp(link.href).then((r) => r.opened || fallback()).catch(fallback);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);
}

export default function App() {
  useInAppLinks();
  const display = useDisplaySettings();
  const online = useOnline();
  const [notice, setNotice] = useState("");
  const [status, setStatus] = useState(null);
  const [services, setServices] = useState([]);
  const [loadingServices, setLoadingServices] = useState(false);
  const [busy, setBusy] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const syncTimerRef = useRef(null);
  const [progress, setProgress] = useState(null);
  const [progressVisible, setProgressVisible] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(""), 6000);
    return () => clearTimeout(t);
  }, [success]);
  const nameOf = (domain) => services.find((s) => s.domain === domain)?.name || domain;
  const [decisions, setDecisions] = useState({});
  const [insights, setInsights] = useState(null);
  const [reviewing, setReviewing] = useState(null);
  const [view, setView] = useState("overview");
  const [order, setOrder] = useState("mixed");
  const [page, setPage] = useState(0);

  const [keeping, setKeeping] = useState(null);
  const [confirmLogout, setConfirmLogout] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Kept senders and confirmed unsubscribes leave the card list (History has them,
  // with Undo). Anything not yet confirmed keeps its card so it can be finished.
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [bulk, setBulk] = useState(null);
  const searchRef = useRef(null);
  const q = query.trim().toLowerCase();
  const shown = orderServices(
    services.filter((s) => {
      const d = decisions[s.domain];
      if (d && (d.decision === "keep" || CONFIRMED.includes(d.status))) return false;
      return !q || [s.name, s.domain, s.sender_email].some((v) => v?.toLowerCase().includes(q));
    }),
    order,
  );
  const toggleSelect = useCallback((domain) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(domain)) next.delete(domain);
      else next.add(domain);
      return next;
    });
  }, []);
  // Only senders still on screen and not yet unsubscribed count as selected.
  const selectable = shown.filter((s) => !decisions[s.domain]?.unsubscribed_at);
  const selectedServices = selectable.filter((s) => selected.has(s.domain));

  // "/" jumps to the search box on All senders, like most apps with search.
  useEffect(() => {
    if (view !== "senders") return;
    const onKey = (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
      if (e.key === "/" && !typing && !document.querySelector(".modal")) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view]);
  const pageCount = Math.max(1, Math.ceil(shown.length / PAGE_SIZE));
  // Clamp: the list can shrink while a background sync refreshes it.
  const currentPage = Math.min(page, pageCount - 1);

  function goToPage(next) {
    setPage(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  const [ack, setAck] = useState(() => localStorage.getItem("privacyAck") === "1");

  function acceptPrivacy() {
    localStorage.setItem("privacyAck", "1");
    setAck(true);
  }

  async function loadDecisions() {
    // One-time move of decisions saved by older builds in localStorage.
    try {
      const legacy = JSON.parse(localStorage.getItem("serviceDecisions") || "{}");
      for (const [domain, decision] of Object.entries(legacy)) {
        if (decision === "keep") await api.setDecision(domain, decision);
      }
      localStorage.removeItem("serviceDecisions");
    } catch {
      /* retried on next load */
    }
    try {
      setDecisions(await api.decisions());
    } catch {
      /* keep current */
    }
  }

  const loadInsights = () => api.insights().then(setInsights).catch(() => {});

  function applyDecision(domain, record) {
    setDecisions((current) => {
      const next = { ...current };
      if (record) next[domain] = record;
      else delete next[domain];
      return next;
    });
    loadInsights();
  }

  async function setDecision(domain, decision) {
    // Clicking the selected choice again clears it.
    const next = decisions[domain]?.decision === decision ? null : decision;
    try {
      applyDecision(domain, await api.setDecision(domain, next));
      return true;
    } catch (e) {
      setError(String(e.message || e));
      return false;
    }
  }

  // Puts a sender back to how it was before any Keep/Unsubscribe choice.
  async function resetDecision(domain) {
    try {
      applyDecision(domain, await api.setDecision(domain, null));
      return true;
    } catch (e) {
      setError(String(e.message || e));
      return false;
    }
  }

  async function undoDecision(domain) {
    if (await resetDecision(domain)) setSuccess(`${nameOf(domain)} is back in your sender list.`);
  }

  // The user's answer when the sender's page was unclear. "No" puts the sender back
  // and reopens the method picker.
  async function answerUnsubscribe(service, worked) {
    try {
      applyDecision(service.domain, await api.answerUnsubscribe(service.domain, worked));
      if (worked) setSuccess(`Unsubscribed from ${service.name}.`);
      else setReviewing(service);
    } catch (e) {
      setError(String(e.message || e));
    }
  }

  // While an unsubscribe window is open, refresh until the app has read the result.
  const waitingOnPage = Object.values(decisions).some((d) => d.status === "in_progress");
  useEffect(() => {
    if (!waitingOnPage) return;
    const timer = setInterval(() => api.decisions().then(setDecisions).catch(() => {}), 1500);
    return () => clearInterval(timer);
  }, [waitingOnPage]);

  // A failed or unfinished unsubscribe: clear it and reopen the method picker.
  async function retryUnsubscribe(service) {
    await resetDecision(service.domain);
    setReviewing(service);
  }

  function finishUnsubscribe(domain, record) {
    setNotice(
      record.fallback_reason
        ? `This sender refused the automatic request (${record.fallback_reason.replace(/\.$/, "")}), so Simplispace opened the sender’s page in a window to send it from there.`
        : "",
    );
    setReviewing(null);
    applyDecision(domain, record);
    if (CONFIRMED.includes(record.status)) setSuccess(`Unsubscribed from ${nameOf(domain)}.`);
    else if (record.status !== "in_progress" && !record.fallback_reason)
      setSuccess(`Unsubscribe request sent to ${nameOf(domain)}. Simplispace will confirm once their emails stop.`);
  }

  // A failed check is not "signed out": keep what we know and try again shortly.
  const refreshStatus = () => api.status().then(setStatus).catch(() => setTimeout(refreshStatus, 1500));

  useEffect(() => {
    refreshStatus();
  }, []);

  useEffect(() => {
    if (status?.connected) {
      loadServices();
      loadDecisions();
      api.syncStatus().then((syncStatus) => {
        if (syncStatus?.running) {
          setBusy(true);
          // If already running past the first page on load, do not show the progress bar
          if (!syncStatus.first_page_done && (syncStatus.page || 0) <= 1) {
            setProgress(syncStatus);
            setProgressVisible(true);
          }
          monitorSync();
        }
      }).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.connected]);

  async function loadServices() {
    setLoadingServices(true);
    try {
      const data = await api.services();
      setServices(data.services);
      loadInsights();
      return data.services.length;
    } catch {
      setError("Couldn’t load your senders. Check that Simplispace is running and try “Sync now”.");
    } finally {
      setLoadingServices(false);
    }
  }

  function monitorSync(initialPage = 0, announce = false) {
    let loadedPage = initialPage;
    let progressBarDismissed = false;

    if (syncTimerRef.current) clearInterval(syncTimerRef.current);
    const timer = setInterval(async () => {
      try {
        const st = await api.syncStatus();
        setProgress(st);

        // Progress bar only runs and completes ONCE for the initial sync batch
        const firstPageDone =
          st.first_page_done ||
          st.phase === "background" ||
          (st.phase === "done" && !st.running) ||
          (st.page > 1);

        if (firstPageDone && !progressBarDismissed) {
          progressBarDismissed = true;
          // Refresh services right away as soon as page 1 completes
          loadedPage = st.page || 1;
          await loadServices();
          // Hide progress bar once completed - stays hidden while remaining sync continues in background
          setTimeout(() => {
            setProgressVisible(false);
          }, 400);
        }

        // Live update on the go: as each subsequent page completes in background, refresh services!
        if (st.running && st.page > loadedPage) {
          loadedPage = st.page;
          await loadServices();
        }

        // When all messages are finished syncing
        if (!st.running) {
          clearInterval(timer);
          setBusy(false);
          setProgressVisible(false);
          setProgress(null);
          if (st.error) {
            // Lost the internet mid-sync: the offline banner says so, and sync resumes
            // on reconnect. Only show the error if the internet is actually fine.
            if (!st.offline || (await checkOnline())) setError(st.error);
            await refreshStatus();
          } else {
            await refreshStatus();
            const count = await loadServices();
            if (announce && count !== undefined) setSuccess(`Sync complete. ${count} senders are up to date.`);
            loadDecisions(); // new mail decides whether unsubscribes worked
          }
        }
      } catch {
        /* transient network hiccup */
      }
    }, 700);
    syncTimerRef.current = timer;
  }

  // Quiet background sync: every time the app opens or the user signs back in, when the
  // connection comes back, and every 30 minutes after that. After the first full index
  // this only fetches what changed in Gmail, so it takes seconds.
  const autoSyncRef = useRef(null);
  autoSyncRef.current = async (force = false) => {
    if (busy || !online || !status?.connected || status.needs_reconnect) return;
    const last = status.last_synced_at ? new Date(status.last_synced_at).getTime() : 0;
    // An unfinished first pass through the inbox picks up right away; otherwise every 30 min.
    if (!force && !status.indexing && Date.now() - last < AUTO_SYNC_MS) return;
    try {
      await api.startSync(false);
    } catch {
      return; // already running or offline; try again next tick
    }
    setBusy(true);
    // The very first sync after signing up shows the progress bar; later ones stay quiet.
    if (!status.last_synced_at) {
      setProgressVisible(true);
      setProgress({ phase: "starting" });
    }
    monitorSync(0);
  };
  // Back online: say so and pick syncing up where it stopped.
  const wasOnline = useRef(online);
  useEffect(() => {
    if (online && !wasOnline.current) {
      setSuccess("You’re back online.");
      setError("");
      autoSyncRef.current(true);
    }
    wasOnline.current = online;
  }, [online]);

  useEffect(() => {
    if (!status?.connected) return;
    // On open / sign-in: always catch up with Gmail. Never synced (just signed up): start
    // now; otherwise give the window a second to draw the saved results first.
    const first = setTimeout(() => autoSyncRef.current(true), status.last_synced_at ? 1000 : 0);
    const every = setInterval(() => autoSyncRef.current(), AUTO_SYNC_MS);
    return () => {
      clearTimeout(first);
      clearInterval(every);
    };
  }, [status?.connected]);

  async function doSync() {
    setBusy(true);
    setError("");
    setProgressVisible(true);
    setProgress({ phase: "starting" });
    try {
      await api.startSync(false);
    } catch (e) {
      setError(String(e.message || e));
      setBusy(false);
      setProgressVisible(false);
      setProgress(null);
      return;
    }
    monitorSync(0, true);
  }

  // Log out keeps data (and the privacy note stays acknowledged); Delete account wipes it.
  async function signOut(deleteData) {
    setConfirmLogout(false);
    setConfirmDelete(false);
    if (syncTimerRef.current) {
      clearInterval(syncTimerRef.current);
      syncTimerRef.current = null;
    }
    setDisconnecting(true);
    try {
      await (deleteData ? api.deleteAccount() : api.logout());
    } catch {}
    if (deleteData) {
      localStorage.removeItem("privacyAck");
      localStorage.removeItem("serviceDecisions");
      setAck(false);
    }
    setDecisions({});
    setInsights(null);
    setReviewing(null);
    setView("overview");
    setServices([]);
    setBusy(false);
    setProgressVisible(false);
    setProgress(null);
    setDisconnecting(false);
    refreshStatus();
  }

  if (!status) return <div className="app"><p>Loading…</p></div>;

  if (!status.connected) return <LoginScreen />;

  if (!ack) return <PrivacyNotice email={status.email} onAccept={acceptPrivacy} />;

  const [title, subtitle] = VIEW_TITLES[view] || ["Page not found", "This page doesn’t exist in Simplispace."];

  function navigate(next) {
    setView(next);
    window.scrollTo({ top: 0 });
  }

  function openCategory(category) {
    setOrder(`only:${category}`);
    setPage(0);
    navigate("senders");
  }

  const emptyInbox = (busy || loadingServices) && services.length === 0
    ? <SkeletonTable />
    : (
      <div className="empty-state">
        <img src="/logo-mark.png" alt="" />
        <h2>No senders yet</h2>
        <p className="muted">Sync your inbox and Simplispace will group your email by sender and score each one.</p>
        <button className="btn primary" onClick={doSync} disabled={busy}>Sync now</button>
      </div>
    );

  return (
    <div className="layout">
      <Sidebar
        view={view}
        onNavigate={navigate}
        status={status}
        busy={busy}
        // First pass through the whole inbox: a quiet count here, scores update as it grows.
        reading={busy && progress?.first_page_done && progress?.has_more
          ? services.reduce((n, s) => n + s.email_count, 0)
          : null}
        onLogout={() => setConfirmLogout(true)}
        loggingOut={disconnecting}
      />

      <main className="main">
        <div className="page-head">
          <div>
            <h1>{title}</h1>
            <p className="muted">{subtitle}</p>
          </div>
          <button
            className="btn primary sync-btn"
            onClick={doSync}
            disabled={busy || !online}
            title={online ? undefined : "Syncing needs an internet connection"}
          >
            <Icon name="sync" className={busy ? "spin" : ""} />
            {busy ? (progressVisible ? "Syncing…" : "Updating…") : "Sync now"}
          </button>
        </div>

        {status.needs_reconnect && (
          <div className="reconnect">
            <div>
              <strong>Gmail connection expired</strong>
              <span className="muted small">Your senders and choices are saved. Reconnect to keep syncing.</span>
            </div>
            {online ? (
              <a className="btn primary sm" href={api.loginUrl()}>Reconnect Gmail</a>
            ) : (
              <button className="btn primary sm" disabled title="Needs an internet connection">Reconnect Gmail</button>
            )}
          </div>
        )}

        {!online && <OfflineBanner />}

        {error && (
          <p className="error" role="alert">
            {error}
            <button className="link" onClick={() => setError("")} aria-label="Dismiss">Dismiss</button>
          </p>
        )}

        {success && (
          <p className="success" role="status">
            {success}
            <button className="link" onClick={() => setSuccess("")} aria-label="Dismiss">Dismiss</button>
          </p>
        )}

        {notice && (
          <p className="notice">
            {notice}
            <button className="link" onClick={() => setNotice("")} aria-label="Dismiss">Dismiss</button>
          </p>
        )}

        {busy && progressVisible && <ProgressBar progress={progress} />}

        {view === "overview" && (
          services.length === 0 ? emptyInbox : (
            <>
              <HealthPanel insights={insights} onImprove={() => openCategory("Marketing/Inactive")} />
              <CategoryTiles services={services} onOpen={openCategory} />
              <StillSending services={services} decisions={decisions} onRetry={retryUnsubscribe} />
              <Candidates
                services={services}
                decisions={decisions}
                onUnsubscribe={setReviewing}
                onSeeAll={() => openCategory("Marketing/Inactive")}
              />
            </>
          )
        )}

        {view === "senders" && (
          services.length === 0 ? emptyInbox : (
            <>
              <div className="list-controls">
                <input
                  ref={searchRef}
                  type="search"
                  className="sender-search"
                  placeholder="Search senders  ( / )"
                  aria-label="Search senders"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setPage(0);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setQuery("");
                    // Down arrow moves from the search box to the first card.
                    if (e.key === "ArrowDown") {
                      e.preventDefault();
                      document.querySelector(".service-grid .service-card")?.focus();
                    }
                  }}
                />
                <label htmlFor="card-order" className="muted small">Sort &amp; filter</label>
                <select
                  id="card-order"
                  value={order}
                  onChange={(e) => {
                    setOrder(e.target.value);
                    setPage(0);
                  }}
                >
                  {Object.entries(ORDER_GROUPS).map(([group, options]) => (
                    <optgroup key={group} label={group}>
                      {Object.entries(options).map(([value, label]) => (
                        <option key={value} value={value}>{label}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </div>

              {selectable.length > 0 && (
                <div className="bulk-bar" aria-live="polite">
                  <label className="bulk-all">
                    <input
                      type="checkbox"
                      checked={selectedServices.length === selectable.length}
                      onChange={(e) =>
                        setSelected(e.target.checked ? new Set(selectable.map((s) => s.domain)) : new Set())}
                    />
                    <span>{selectedServices.length > 0 ? `${selectedServices.length} selected` : `Select all ${selectable.length}`}</span>
                  </label>
                  {selectedServices.length > 0 && (
                    <>
                      <button className="btn danger-solid small-btn" disabled={!online} onClick={() => setBulk(selectedServices)}>
                        Unsubscribe from {selectedServices.length}
                      </button>
                      <button className="btn small-btn" onClick={() => setSelected(new Set())}>Clear</button>
                    </>
                  )}
                </div>
              )}

              {shown.length === 0 ? (
                <p className="muted">{q ? `No senders match “${query.trim()}”.` : "No senders in this group."}</p>
              ) : (
                <div className="service-grid">
                  {shown.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE).map((service) => (
                    <ServiceCard
                      key={service.domain}
                      service={service}
                      decision={decisions[service.domain]}
                      onUnsubscribe={setReviewing}
                      onKeep={setKeeping}
                      onReset={resetDecision}
                      onRetry={retryUnsubscribe}
                      onAnswer={answerUnsubscribe}
                      showLogos={display.logos}
                      selected={selected.has(service.domain)}
                      onSelect={toggleSelect}
                    />
                  ))}
                </div>
              )}

              {pageCount > 1 && (
                <Pager page={currentPage} count={pageCount} onGo={goToPage} />
              )}
            </>
          )
        )}

        {view === "history" && (
          <HistoryView
            services={services}
            decisions={decisions}
            onUndoKeep={undoDecision}
            onReset={undoDecision}
          />
        )}

        {view === "profile" && (
          <ProfileView
            status={status}
            services={services}
            busy={busy}
            onSync={doSync}
            onLogout={() => setConfirmLogout(true)}
            loggingOut={disconnecting}
          />
        )}

        {view === "privacy" && <PrivacyView />}

        {view === "settings" && (
          <SettingsView display={display} email={status.email} onDeleteAccount={() => setConfirmDelete(true)} />
        )}

        {!VIEW_TITLES[view] && (
          <div className="empty-state">
            <p className="big-404">404</p>
            <h2>Page not found</h2>
            <p className="muted">This part of Simplispace doesn’t exist.</p>
            <button className="btn primary" onClick={() => navigate("overview")}>Go to Overview</button>
          </div>
        )}
      </main>

      {confirmLogout && (
        <LogoutDialog email={status.email} onCancel={() => setConfirmLogout(false)} onConfirm={() => signOut(false)} />
      )}

      {confirmDelete && (
        <DeleteAccountDialog
          email={status.email}
          senders={services.length}
          choices={Object.keys(decisions).length}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => signOut(true)}
        />
      )}

      {keeping && (
        <KeepDialog
          service={keeping}
          onCancel={() => setKeeping(null)}
          onConfirm={async () => {
            if (await setDecision(keeping.domain, "keep"))
              setSuccess(`${keeping.name} kept. It won’t show up again; find it under History.`);
            setKeeping(null);
          }}
        />
      )}

      {bulk && (
        <BulkUnsubscribeDialog
          services={bulk}
          onDone={applyDecision}
          onClose={() => {
            setBulk(null);
            setSelected(new Set());
          }}
        />
      )}

      {reviewing && (
        <UnsubscribeDialog
          service={reviewing}
          onClose={() => setReviewing(null)}
          onDone={finishUnsubscribe}
        />
      )}
    </div>
  );
}
