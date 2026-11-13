// The desktop app serves this page from its own API, so calls go to the same origin
// (a different host name, e.g. 127.0.0.1 vs localhost, is a different site to the
// browser and gets refused). Only the Vite dev server on :5173 needs the full address.
const BASE = import.meta.env.VITE_API_BASE || (location.port === "5173" ? "http://localhost:8000" : "");

async function json(path, opts = {}) {
  const res = await fetch(BASE + path, {
    credentials: "include",
    ...opts,
    // The API rejects state-changing requests without this header (cross-site protection).
    headers: {
      "X-Simplispace": "1",
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      ...opts.headers,
    },
  });
  if (!res.ok) {
    let message = await res.text();
    try {
      message = JSON.parse(message).detail || message;
    } catch {
      /* plain-text error */
    }
    throw new Error(typeof message === "string" && message ? message : res.statusText);
  }
  return res.json();
}

const enc = encodeURIComponent;

export const api = {
  base: BASE,
  status: () => json("/auth/status"),
  startSync: (nextPage = false) =>
    json(`/sync?next_page=${nextPage === true ? "true" : "false"}`, { method: "POST" }),
  syncStatus: () => json("/sync/status"),
  services: () => json("/services"),
  insights: () => json("/insights"),
  decisions: () => json("/decisions"),
  setDecision: (domain, decision) =>
    json(`/decisions/${enc(domain)}`, { method: "PUT", body: JSON.stringify({ decision }) }),
  unsubscribeOptions: (domain) => json(`/unsubscribe/${enc(domain)}`),
  unsubscribe: (domain, method, noFallback = false) =>
    json(`/unsubscribe/${enc(domain)}`, {
      method: "POST",
      body: JSON.stringify({ method, no_fallback: noFallback }),
    }),
  senderEmails: (domain) => json(`/services/${enc(domain)}/emails`),
  answerUnsubscribe: (domain, worked) =>
    json(`/unsubscribe/${enc(domain)}/answer`, { method: "POST", body: JSON.stringify({ worked }) }),
  openInApp: (url) => json("/open-in-app", { method: "POST", body: JSON.stringify({ url }) }),
  logout: () => json("/auth/logout", { method: "POST" }),
  deleteAccount: () => json("/auth/delete-account", { method: "POST" }),
  loginUrl: () => BASE + "/auth/login",
};
