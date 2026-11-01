import React, { useState } from "react";
import { usePolling } from "../hooks.js";
import { errorMessage } from "../format.js";
import { ErrorBanner } from "./Common.jsx";

export default function Admin({ api, role }) {
  const n = usePolling((signal) => api.notifications({ limit: 50 }, { signal }), [], 10000);
  const au = usePolling((signal) => (role === "admin" ? api.audit({ limit: 100 }, { signal }) : Promise.resolve(null)), [role], 15000);
  const [msg, setMsg] = useState("");
  const requeue = async () => { try { setMsg(`Requeued ${(await api.requeueNotifications()).requeued} failed notification(s).`); } catch (e) { setMsg(errorMessage(e)); } };
  return (
    <section>
      <div className="card">
        <h3>Notifications</h3>
        <ErrorBanner error={n.error} />
        {role === "admin" && <button onClick={requeue}>Retry failed notifications</button>} {msg && <span className="muted">{msg}</span>}
        <table className="table"><thead><tr><th>Status</th><th>Channel</th><th>Kind</th><th>Incident</th><th>Attempts</th><th>Last error</th></tr></thead>
          <tbody>{(n.data || []).map((x) => <tr key={x.id}><td>{x.status}</td><td>{x.channel}</td><td>{x.kind}</td><td>{x.incident_id}</td><td>{x.attempts}</td><td className="small">{x.last_error || ""}</td></tr>)}</tbody></table>
      </div>
      {role === "admin" && (
        <div className="card">
          <h3>Audit log {au.data && (au.data.chain_valid ? <span className="badge st-resolved">chain intact</span> : <span className="badge sev-critical">CHAIN BROKEN – investigate</span>)}</h3>
          <ErrorBanner error={au.error} />
          <table className="table"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Outcome</th></tr></thead>
            <tbody>{(au.data?.items || []).map((x, i) => <tr key={i}><td>{new Date(x.at * 1000).toLocaleString()}</td><td>{x.actor} ({x.role})</td><td>{x.action}</td><td>{x.target}</td><td>{x.outcome}</td></tr>)}</tbody></table>
        </div>
      )}
    </section>
  );
}
