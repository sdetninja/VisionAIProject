import React, { useCallback, useMemo, useState } from "react";
import { createApi } from "./api.js";
import { decodeClaims, makeTokenStore } from "./auth.js";
import Login from "./components/Login.jsx";
import Queue from "./components/Queue.jsx";
import IncidentDetail from "./components/IncidentDetail.jsx";
import Cameras from "./components/Cameras.jsx";
import Admin from "./components/Admin.jsx";

const store = makeTokenStore(window.sessionStorage);

export default function App() {
  const [token, setToken] = useState(store.get());
  const [view, setView] = useState({ name: "queue" });
  const logout = useCallback(() => { store.clear(); setToken(null); setView({ name: "queue" }); }, []);
  const api = useMemo(() => createApi({ getToken: () => store.get() || "", onUnauthorized: logout }), [logout]);
  if (!token) return <Login onToken={(t) => { store.set(t); setToken(t); }} />;

  const claims = decodeClaims(token) || {};   // display only; the server enforces permissions
  const role = Array.isArray(claims.role) ? claims.role[0] : claims.role;
  const tab = (name, label) => <button className={view.name === name ? "tab active" : "tab"} onClick={() => setView({ name })}>{label}</button>;
  return (
    <div className="app">
      <header>
        <strong>VisionGuard</strong>
        <nav>{tab("queue", "Incidents")}{tab("cameras", "Cameras")}{role !== "viewer" && tab("admin", role === "admin" ? "Admin" : "Notifications")}</nav>
        <span className="muted">{claims.sub} · {role}</span>
        <button onClick={logout}>Sign out</button>
      </header>
      <main>
        {view.name === "queue" && <Queue api={api} onOpen={(id) => setView({ name: "incident", id })} />}
        {view.name === "incident" && <IncidentDetail api={api} id={view.id} role={role} onBack={() => setView({ name: "queue" })} />}
        {view.name === "cameras" && <Cameras api={api} />}
        {view.name === "admin" && <Admin api={api} role={role} />}
      </main>
    </div>
  );
}
