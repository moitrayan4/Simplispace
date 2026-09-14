const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function json(path, opts) {
  const res = await fetch(BASE + path, { credentials: "include", ...opts });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json();
}

export const api = {
  base: BASE,
  status: () => json("/auth/status"),
  startSync: () => json("/sync", { method: "POST" }),
  syncStatus: () => json("/sync/status"),
  services: () => json("/services"),
  disconnect: () => json("/auth/disconnect", { method: "POST" }),
  loginUrl: () => BASE + "/auth/login",
};
