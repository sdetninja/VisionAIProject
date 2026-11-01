import React, { useEffect, useState } from "react";
import { isSafeEvidencePath } from "../api.js";

// Images need the Authorization header, so they are fetched as blobs (an <img src> cannot send it).
export default function Evidence({ api, paths }) {
  const [urls, setUrls] = useState([]);
  const [failed, setFailed] = useState(0);
  const [open, setOpen] = useState(null);
  const key = (paths || []).join("|");
  useEffect(() => {
    let alive = true;
    const made = [];
    setUrls([]); setFailed(0);
    (paths || []).filter(isSafeEvidencePath).forEach(async (p, idx) => {
      try {
        const blob = await api.evidence(p);
        const u = URL.createObjectURL(blob);
        made.push(u);
        if (alive) setUrls((cur) => { const n = [...cur]; n[idx] = u; return n; });
      } catch { if (alive) setFailed((f) => f + 1); }
    });
    return () => { alive = false; made.forEach(URL.revokeObjectURL); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  if (!paths || paths.length === 0) return <p className="muted">No evidence images were captured for this incident.</p>;
  return (
    <div>
      <div className="gallery">
        {urls.map((u, i) => u && <img key={i} src={u} alt={`Evidence frame ${i + 1}`} onClick={() => setOpen(u)} />)}
      </div>
      {failed > 0 && <p className="muted">{failed} image(s) could not be loaded (may have expired).</p>}
      {open && <div className="lightbox" onClick={() => setOpen(null)} role="dialog"><img src={open} alt="Evidence enlarged" /></div>}
    </div>
  );
}
