import React, { useState } from "react";
import { usePolling } from "../hooks.js";
import { ACTION_LABEL, actionsFor, describeIncident, errorMessage } from "../format.js";
import { ErrorBanner, SeverityBadge, StatusBadge } from "./Common.jsx";
import Evidence from "./Evidence.jsx";

export default function IncidentDetail({ api, id, role, onBack }) {
  const { data: inc, error } = usePolling((signal) => api.incident(id, { signal }), [id], 5000);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [local, setLocal] = useState(null);
  const cur = local && inc && local.incident_id === inc.incident_id && local.status !== inc.status ? local : inc;

  const act = async (action) => {
    if (action === "reject" && !comment.trim()) return setMsg("Please add a short reason for marking this a false positive.");
    setBusy(true); setMsg("");
    try { setLocal(await api.review(id, action, comment.trim())); setComment(""); }
    catch (e) { setMsg(errorMessage(e)); }
    finally { setBusy(false); }
  };

  if (!cur) return <section><button className="link" onClick={onBack}>← Back</button><ErrorBanner error={error} />{!error && <p className="muted">Loading…</p>}</section>;
  const a = cur.analysis && !cur.analysis.unavailable ? cur.analysis : null;
  const actions = actionsFor(cur.status, role);
  return (
    <section>
      <button className="link" onClick={onBack}>← Back to queue</button>
      <ErrorBanner error={error} />
      <h2>{cur.incident_id}: {describeIncident(cur)} <StatusBadge status={cur.status} /> <SeverityBadge severity={a?.severity} /></h2>
      <p className="muted">{cur.camera_id} / {cur.zone} · first seen {cur.first_event_time} · {cur.event_count} event(s)</p>
      {cur.needs_review && <div className="banner warn">The detector was not certain about this event. Please check the images before confirming.</div>}

      <div className="grid">
        <div className="card"><h3>Evidence</h3><Evidence api={api} paths={cur.evidence} />{cur.evidence_status === "missing" && <p className="muted">Frames were no longer available when the incident was created.</p>}</div>
        <div className="card">
          <h3>AI assessment <span className="badge advisory">advisory only</span></h3>
          {cur.analysis?.unavailable && <p className="muted">Analysis is not available yet.</p>}
          {a && (<>
            <p><strong>Risk {a.risk_score}/100.</strong> {a.summary}</p>
            {!a.grounded && <div className="banner warn">No matching policy was found. Treat this as unverified.</div>}
            {a.recommended_actions?.length > 0 && <><h4>Suggested actions</h4><ul>{a.recommended_actions.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
            {a.citations?.length > 0 && <><h4>Policy references</h4><ul>{a.citations.map((c) => <li key={c.id}>{c.source}: {c.heading}</li>)}</ul></>}
            <details><summary>How the score was calculated</summary><ul>{(a.risk_factors || []).map((f, i) => <li key={i}>{f}</li>)}</ul></details>
          </>)}
          <p className="muted small">The system detects and advises; people decide. Severity is calculated by fixed rules, not by the language model.</p>
        </div>
      </div>

      <div className="card">
        <h3>Review</h3>
        {actions.length === 0
          ? <p className="muted">{role === "viewer" ? "You have read-only access." : "No further actions for this status."}</p>
          : (<>
            <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} maxLength={1000} placeholder="Comment (required for false positive)" aria-label="Review comment" />
            <div className="actions">{actions.map((x) => <button key={x} disabled={busy} className={x === "confirm" ? "primary" : ""} onClick={() => act(x)}>{ACTION_LABEL[x]}</button>)}</div>
          </>)}
        {msg && <div className="banner error" role="alert">{msg}</div>}
        <h4>History</h4>
        <ul className="history">{(cur.history || []).map((h, i) => <li key={i}>{h.at} · {h.actor || "system"} · {h.from_status || "–"} → {h.to_status}{h.comment ? ` · “${h.comment}”` : ""}</li>)}</ul>
      </div>
    </section>
  );
}
