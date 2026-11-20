import React from "react";
import { usePolling } from "../hooks.js";
import { ErrorBanner } from "./Common.jsx";

// Admin only. (v3: the notifications panel is gone with the notification service.)
export default function Admin({ api }) {
  const au = usePolling((signal) => api.audit({ limit: 100 }, { signal }), [], 15000);
  return (
    <section>
      <div className="card">
        <h3>Audit log {au.data && (au.data.chain_valid ? <span className="badge st-resolved">chain intact</span> : <span className="badge sev-critical">CHAIN BROKEN – investigate</span>)}</h3>
        <ErrorBanner error={au.error} />
        <table className="table"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Outcome</th></tr></thead>
          <tbody>{(au.data?.items || []).map((x, i) => <tr key={i}><td>{new Date(x.at * 1000).toLocaleString()}</td><td>{x.actor} ({x.role})</td><td>{x.action}</td><td>{x.target}</td><td>{x.outcome}</td></tr>)}</tbody></table>
      </div>
    </section>
  );
}
