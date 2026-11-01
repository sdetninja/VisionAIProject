export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export function createApi({ baseUrl = "", getToken, onUnauthorized = () => {}, fetchImpl = (...a) => fetch(...a) }) {
  async function call(path, { method = "GET", body, params, raw = false, signal } = {}) {
    const qs = params
      ? "?" + Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&")
      : "";
    const headers = { Authorization: `Bearer ${getToken()}` };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let res;
    try {
      res = await fetchImpl(baseUrl + path + (qs === "?" ? "" : qs), { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, signal });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError(0, "Cannot reach the server.");
    }
    if (res.status === 401) onUnauthorized();
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* non-JSON error body */ }
      throw new ApiError(res.status, detail);
    }
    return raw ? res.blob() : res.json();
  }
  return {
    me: (o) => call("/api/me", o),
    cameras: (o) => call("/api/cameras", o),
    incidents: (params, o) => call("/api/incidents", { ...o, params }),
    incident: (id, o) => call(`/api/incidents/${encodeURIComponent(id)}`, o),
    evidence: (path, o) => call(path, { ...o, raw: true }),    // path comes from the gateway response
    review: (id, action, comment) => call(`/api/incidents/${encodeURIComponent(id)}/review`, { method: "POST", body: { action, comment: comment || undefined } }),
    notifications: (params, o) => call("/api/notifications", { ...o, params }),
    requeueNotifications: () => call("/api/notifications/requeue-dead", { method: "POST" }),
    audit: (params, o) => call("/api/audit", { ...o, params }),
  };
}

// Only same-origin gateway paths may be fetched as evidence (defence against a poisoned response).
export function isSafeEvidencePath(p) { return typeof p === "string" && /^\/api\/incidents\/[^/]+\/evidence\/\d+$/.test(p); }
