import React, { useState } from "react";
import { usePolling } from "../hooks.js";
import { describeIncident, timeAgo } from "../format.js";
import { ErrorBanner, StatusBadge } from "./Common.jsx";

const PAGE = 25;

export default function Queue({ api, onOpen }) {
  const [status, setStatus] = useState("OPEN");
  const [needsReview, setNeedsReview] = useState(false);
  const [camera, setCamera] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, loading } = usePolling(
    (signal) => api.incidents({ status: status || undefined, needs_review: needsReview ? true : undefined, camera_id: camera.trim() || undefined, limit: PAGE, offset }, { signal }),
    [status, needsReview, camera, offset], 5000);
  const reset = (fn) => (e) => { setOffset(0); fn(e); };
  const items = data?.items || [];
  const total = data?.total;
  return (
    <section>
      <div className="toolbar">
        <label>Status
          <select value={status} onChange={reset((e) => setStatus(e.target.value))}>
            <option value="">All</option><option value="OPEN">Open</option><option value="UNDER_REVIEW">Under review</option>
            <option value="CONFIRMED">Confirmed</option><option value="FALSE_POSITIVE">False positive</option><option value="RESOLVED">Resolved</option>
          </select>
        </label>
        <label>Camera <input value={camera} onChange={reset((e) => setCamera(e.target.value))} placeholder="CAM_001" /></label>
        <label className="check"><input type="checkbox" checked={needsReview} onChange={reset((e) => setNeedsReview(e.target.checked))} /> Needs review only</label>
        <span className="muted">{loading ? "Loading…" : total != null ? `${total} incidents` : `${items.length} shown`}</span>
      </div>
      <ErrorBanner error={error} />
      <table className="table">
        <thead><tr><th>ID</th><th>What</th><th>Where</th><th>Status</th><th>Seen</th><th></th></tr></thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.incident_id} onClick={() => onOpen(i.incident_id)} className="row">
              <td><button className="link" onClick={(e) => { e.stopPropagation(); onOpen(i.incident_id); }}>{i.incident_id}</button></td>
              <td>{describeIncident(i)}{i.event_count > 1 && <span className="muted"> ×{i.event_count}</span>}{i.needs_review && <span className="badge warn">needs review</span>}</td>
              <td>{i.camera_id} / {i.zone}</td>
              <td><StatusBadge status={i.status} /></td>
              <td title={i.last_event_time}>{timeAgo(i.last_event_time)}</td>
              <td>{i.thumbnail ? "📷" : ""}</td>
            </tr>
          ))}
          {!loading && items.length === 0 && <tr><td colSpan={6} className="muted center">No incidents match.</td></tr>}
        </tbody>
      </table>
      <div className="pager">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
        <button disabled={items.length < PAGE} onClick={() => setOffset(offset + PAGE)}>Next</button>
      </div>
    </section>
  );
}
