export const SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
export const STATUS_LABEL = {
  OPEN: "Open", UNDER_REVIEW: "Under review", CONFIRMED: "Confirmed", FALSE_POSITIVE: "False positive", RESOLVED: "Resolved",
};

// Which review actions to offer for a status (server still enforces; this only avoids dead buttons).
export function actionsFor(status, role) {
  if (role !== "reviewer" && role !== "admin") return [];
  switch (status) {
    case "OPEN": return ["start_review", "confirm", "reject"];
    case "UNDER_REVIEW": return ["confirm", "reject"];
    case "CONFIRMED": return ["resolve"];
    default: return [];
  }
}
export const ACTION_LABEL = { start_review: "Start review", confirm: "Confirm", reject: "Mark false positive", resolve: "Resolve" };

export function describeIncident(i) {
  const type = (i.event_type || "").replace(/_/g, " ").toLowerCase();
  if (i.event_type === "PPE_VIOLATION") return `Missing ${(i.missing_ppe || []).join(", ") || "PPE"}`;
  if (i.event_type === "HAZARD_DETECTED") return `Possible ${i.hazard_class || "hazard"}`;
  return type.charAt(0).toUpperCase() + type.slice(1);
}

export function timeAgo(iso, now = Date.now()) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function errorMessage(e) {
  if (!e) return "";
  if (e.status === 401) return "Your session has expired. Please sign in again.";
  if (e.status === 403) return "You do not have permission to do that.";
  if (e.status === 404) return "Not found (or outside your camera scope).";
  if (e.status === 409) return "That action is not allowed in the incident's current state. Refresh and try again.";
  if (e.status === 429) return "Too many requests. Please wait a moment.";
  if (e.status === 503 || e.status === 502) return "A backend service is unavailable. Retrying automatically.";
  return e.message || "Something went wrong.";
}
