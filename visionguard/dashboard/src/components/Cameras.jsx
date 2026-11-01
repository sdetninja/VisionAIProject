import React from "react";
import { usePolling } from "../hooks.js";
import { ErrorBanner } from "./Common.jsx";

export default function Cameras({ api }) {
  const { data, error, loading } = usePolling((signal) => api.cameras({ signal }), [], 10000);
  const list = Array.isArray(data) ? data : [];
  return (
    <section>
      <ErrorBanner error={error} />
      {loading && <p className="muted">Loading…</p>}
      <div className="grid cams">
        {list.map((c) => {
          const o = typeof c === "string" ? { camera_id: c } : c;
          return <div className="card" key={o.camera_id}><h3>{o.camera_id}</h3>{Object.entries(o).filter(([k]) => k !== "camera_id" && typeof o[k] !== "object").map(([k, v]) => <div key={k} className="kv"><span className="muted">{k}</span> {String(v)}</div>)}</div>;
        })}
      </div>
      {!loading && list.length === 0 && <p className="muted">No cameras visible to you.</p>}
    </section>
  );
}
