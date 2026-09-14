import { Fragment, useEffect, useState } from "react";
import { api } from "./api";
import "./App.css";

const CATEGORY_CLASS = {
  Important: "cat-important",
  Useful: "cat-useful",
  Optional: "cat-optional",
  "Marketing/Inactive": "cat-marketing",
};

const PHASE_LABEL = {
  starting: "Starting…",
  listing: "Finding your emails…",
  fetching: "Fetching messages",
  scanning_sent: "Checking your replies…",
  done: "Scoring…",
};

function ProgressBar({ progress }) {
  const p = progress || {};
  const label = PHASE_LABEL[p.phase] || "Working…";
  const hasTotal = p.phase === "fetching" && p.total > 0;
  const pct = hasTotal ? Math.round((p.done / p.total) * 100) : null;
  return (
    <div className="progress">
      <div className="progress-head">
        <span>{label}</span>
        <span className="muted">{hasTotal ? `${p.done}/${p.total} (${pct}%)` : ""}</span>
      </div>
      <div className="progress-track">
        <div
          className={"progress-fill" + (hasTotal ? "" : " indeterminate")}
          style={hasTotal ? { width: pct + "%" } : undefined}
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

        <a className="btn google" href={api.loginUrl()}>
          <GoogleIcon />
          <span>Continue with Google</span>
        </a>

        <ul className="login-points">
          <li><span className="dot" /> We only read your mail. We never send or delete anything.</li>
          <li><span className="dot" /> Nothing gets unsubscribed until you say so.</li>
          <li><span className="dot" /> What your emails say never leaves your inbox.</li>
        </ul>

        <p className="login-fine">Sign in with Google to get started.</p>
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

export default function App() {
  const [status, setStatus] = useState(null);
  const [services, setServices] = useState([]);
  const [loadingServices, setLoadingServices] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(null);

  const refreshStatus = () => api.status().then(setStatus).catch(() => setStatus({ connected: false }));

  useEffect(() => {
    refreshStatus();
  }, []);

  useEffect(() => {
    if (status?.connected) loadServices();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.connected]);

  async function loadServices() {
    setLoadingServices(true);
    try {
      const data = await api.services();
      setServices(data.services);
    } catch {
      setServices([]);
    } finally {
      setLoadingServices(false);
    }
  }

  async function doSync() {
    setBusy(true);
    setError("");
    setProgress({ phase: "starting" });
    try {
      await api.startSync();
    } catch (e) {
      setError(String(e.message || e));
      setBusy(false);
      setProgress(null);
      return;
    }
    const timer = setInterval(async () => {
      try {
        const st = await api.syncStatus();
        setProgress(st);
        if (!st.running) {
          clearInterval(timer);
          setBusy(false);
          setProgress(null);
          if (st.error) {
            setError(st.error);
            await refreshStatus();   // token may have been dropped -> back to login
          } else {
            await refreshStatus();
            await loadServices();
          }
        }
      } catch {
        /* keep polling; transient */
      }
    }, 700);
  }

  async function doDisconnect() {
    if (!confirm("Disconnect Gmail and delete stored data?")) return;
    await api.disconnect();
    setServices([]);
    refreshStatus();
  }

  if (!status) return <div className="app"><p>Loading…</p></div>;

  if (!status.connected) return <LoginScreen />;

  return (
    <div className="app">
      <header>
        <span className="logo-badge">
          <img src="/logo-mark.png" alt="Simplispace" />
        </span>
        <h1>Simplispace</h1>
      </header>

      <>
          <div className="bar">
            <div>
              <strong>{status.email}</strong>
              <span className="muted">
                {status.last_synced_at
                  ? ` · synced ${new Date(status.last_synced_at).toLocaleString()}`
                  : " · not synced yet"}
              </span>
            </div>
            <div className="actions">
              <button className="btn primary" onClick={doSync} disabled={busy}>
                {busy ? "Syncing…" : "Sync now"}
              </button>
              <button className="btn" onClick={doDisconnect} disabled={busy}>
                Disconnect
              </button>
            </div>
          </div>

          {error && <p className="error">{error}</p>}

          {busy && <ProgressBar progress={progress} />}

          {(busy || loadingServices) && services.length === 0 ? (
            <SkeletonTable />
          ) : services.length === 0 ? (
            <p className="muted">No services yet. Click “Sync now” to fetch and score your inbox.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Service</th>
                  <th>Category</th>
                  <th>Score</th>
                  <th>Emails</th>
                  <th>Open rate</th>
                  <th>Last email</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {services.map((s) => (
                  <Fragment key={s.domain}>
                    <tr>
                      <td>
                        <div className="svc-name">{s.name}</div>
                        <div className="muted small">{s.domain}</div>
                      </td>
                      <td>
                        <span className={`badge ${CATEGORY_CLASS[s.category] || ""}`}>
                          {s.category}
                        </span>
                      </td>
                      <td className="num">
                        <span className={`score-chip ${CATEGORY_CLASS[s.category] || ""}`}>
                          {s.score}
                        </span>
                      </td>
                      <td className="num">{s.email_count}</td>
                      <td className="num">{Math.round(s.open_rate * 100)}%</td>
                      <td className="num">
                        {s.days_since_last == null ? "never" : `${s.days_since_last}d ago`}
                      </td>
                      <td>
                        <button
                          className="link"
                          onClick={() => setExpanded(expanded === s.domain ? null : s.domain)}
                        >
                          {s.signals.length} signal{s.signals.length !== 1 ? "s" : ""}
                        </button>
                      </td>
                    </tr>
                    {expanded === s.domain && (
                      <tr className="detail">
                        <td colSpan={7}>
                          {s.signals.length === 0 ? (
                            <em>No signals. This is the neutral baseline score.</em>
                          ) : (
                            <ul>
                              {s.signals.map((sig) => (
                                <li key={sig.key}>
                                  {sig.label}{" "}
                                  <span className={sig.points >= 0 ? "pos" : "neg"}>
                                    {sig.points >= 0 ? "+" : ""}
                                    {sig.points}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
      </>
    </div>
  );
}
