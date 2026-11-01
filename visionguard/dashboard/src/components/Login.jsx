import React, { useState } from "react";
import { decodeClaims, isExpired } from "../auth.js";

export default function Login({ onToken }) {
  const [t, setT] = useState("");
  const [err, setErr] = useState("");
  const submit = (e) => {
    e.preventDefault();
    const tok = t.trim();
    if (!decodeClaims(tok)) return setErr("That does not look like a valid token.");
    if (isExpired(tok)) return setErr("That token has expired.");
    onToken(tok);
  };
  return (
    <form className="card login" onSubmit={submit}>
      <h1>VisionGuard</h1>
      <p className="muted">Paste your access token. (With an identity provider this screen is replaced by single sign-on.)</p>
      <textarea value={t} onChange={(e) => setT(e.target.value)} rows={5} aria-label="Access token" autoComplete="off" spellCheck={false} />
      {err && <div className="banner error" role="alert">{err}</div>}
      <button type="submit" className="primary">Sign in</button>
    </form>
  );
}
