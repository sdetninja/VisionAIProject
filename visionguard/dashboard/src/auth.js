// Display-only token handling. The gateway verifies tokens; the browser never trusts these claims for security.
const KEY = "vg_token";

export function decodeClaims(token) {
  try {
    const p = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(p + "=".repeat((4 - (p.length % 4)) % 4));
    return JSON.parse(decodeURIComponent(escape(json)));
  } catch { return null; }
}

export function isExpired(token, now = Date.now()) {
  const c = decodeClaims(token);
  return !c || typeof c.exp !== "number" || c.exp * 1000 <= now;
}

// sessionStorage: cleared when the tab closes. Trade-off vs memory-only: survives reload but is readable by any
// script on the page, which is why index.html ships a strict CSP and the app renders all data as text.
export function makeTokenStore(storage) {
  return {
    get() { const t = storage.getItem(KEY); if (t && isExpired(t)) { storage.removeItem(KEY); return null; } return t; },
    set(t) { storage.setItem(KEY, t.trim()); },
    clear() { storage.removeItem(KEY); },
  };
}
