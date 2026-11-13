import { useSyncExternalStore } from "react";

// Internet reachability, shared by the whole app. The OS "offline" event is instant
// but "online" only means a network adapter is up (Wi-Fi with no internet still says
// online), so every change is confirmed by actually reaching Google: the same
// lightweight check Android and Chrome use. Rechecked every 30 s, every 5 s while offline.
const PROBE = "https://clients3.google.com/generate_204";
let online = navigator.onLine;
const listeners = new Set();
let lastCheck = 0;

function set(value) {
  if (value === online) return;
  online = value;
  listeners.forEach((notify) => notify());
}

export async function checkOnline() {
  lastCheck = Date.now();
  if (!navigator.onLine) {
    set(false);
    return false;
  }
  try {
    await fetch(PROBE, { mode: "no-cors", cache: "no-store", signal: AbortSignal.timeout(5000) });
    set(true);
  } catch {
    set(false);
  }
  return online;
}

window.addEventListener("offline", () => set(false));
window.addEventListener("online", checkOnline);
setInterval(() => {
  if (!online || Date.now() - lastCheck > 30000) checkOnline();
}, 5000);
checkOnline();

export function useOnline() {
  return useSyncExternalStore(
    (notify) => {
      listeners.add(notify);
      return () => listeners.delete(notify);
    },
    () => online,
  );
}
