import React from "react";
import { STATUS_LABEL, errorMessage } from "../format.js";

export function SeverityBadge({ severity }) {
  if (!severity) return <span className="badge sev-none">Pending</span>;
  return <span className={`badge sev-${severity.toLowerCase()}`}>{severity}</span>;
}
export function StatusBadge({ status }) {
  return <span className={`badge st-${(status || "").toLowerCase()}`}>{STATUS_LABEL[status] || status}</span>;
}
export function ErrorBanner({ error }) {
  if (!error) return null;
  return <div className="banner error" role="alert">{errorMessage(error)}</div>;
}
