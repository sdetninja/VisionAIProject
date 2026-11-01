import test from "node:test";
import assert from "node:assert/strict";
import { createApi, ApiError, isSafeEvidencePath } from "../src/api.js";
import { decodeClaims, isExpired, makeTokenStore } from "../src/auth.js";
import { actionsFor, describeIncident, timeAgo, errorMessage } from "../src/format.js";
import { startPolling } from "../src/polling.js";

const jwt = (c) => "h." + Buffer.from(JSON.stringify(c)).toString("base64url") + ".s";
const resp = (status, body, blob) => ({ ok: status < 400, status, statusText: "x", json: async () => body, blob: async () => blob });

test("api: builds URL, auth header, drops empty params", async () => {
  let seen;
  const api = createApi({ getToken: () => "TOK", fetchImpl: async (u, o) => { seen = { u, o }; return resp(200, { ok: 1 }); } });
  await api.incidents({ status: "OPEN", camera_id: "", needs_review: undefined, limit: 25, offset: 0 });
  assert.equal(seen.u, "/api/incidents?status=OPEN&limit=25&offset=0");
  assert.equal(seen.o.headers.Authorization, "Bearer TOK");
});

test("api: encodes ids and posts review without actor", async () => {
  let seen;
  const api = createApi({ getToken: () => "T", fetchImpl: async (u, o) => { seen = { u, o }; return resp(200, {}); } });
  await api.review("INC/1", "confirm", "ok");
  assert.equal(seen.u, "/api/incidents/INC%2F1/review");
  assert.deepEqual(JSON.parse(seen.o.body), { action: "confirm", comment: "ok" });
  assert.equal("actor" in JSON.parse(seen.o.body), false);
});

test("api: 401 triggers onUnauthorized; errors carry status and detail", async () => {
  let out = 0;
  const api = createApi({ getToken: () => "T", onUnauthorized: () => out++, fetchImpl: async () => resp(401, { detail: "token expired" }) });
  await assert.rejects(api.cameras(), (e) => e instanceof ApiError && e.status === 401 && e.message === "token expired");
  assert.equal(out, 1);
});

test("api: network failure becomes ApiError(0); abort is passed through", async () => {
  const api = createApi({ getToken: () => "T", fetchImpl: async () => { throw new TypeError("fail"); } });
  await assert.rejects(api.cameras(), (e) => e.status === 0);
  const ab = createApi({ getToken: () => "T", fetchImpl: async () => { const e = new Error("a"); e.name = "AbortError"; throw e; } });
  await assert.rejects(ab.cameras(), (e) => e.name === "AbortError");
});

test("api: evidence returns blob; path allow-list", async () => {
  const api = createApi({ getToken: () => "T", fetchImpl: async () => resp(200, null, "BLOB") });
  assert.equal(await api.evidence("/api/incidents/INC-1/evidence/0"), "BLOB");
  assert.ok(isSafeEvidencePath("/api/incidents/INC-000001/evidence/2"));
  for (const bad of ["http://evil.com/x", "//evil.com/api/incidents/a/evidence/0", "/api/incidents/a/b/evidence/0", "/api/labels", "/api/incidents/a/evidence/x", null])
    assert.equal(isSafeEvidencePath(bad), false, String(bad));
});

test("auth: decode, expiry, store drops expired token", () => {
  const good = jwt({ sub: "a", exp: Date.now() / 1000 + 100 }), old = jwt({ sub: "a", exp: 5 });
  assert.equal(decodeClaims(good).sub, "a");
  assert.equal(decodeClaims("garbage"), null);
  assert.equal(isExpired(good), false);
  assert.equal(isExpired(old), true);
  assert.equal(isExpired(jwt({ sub: "a" })), true);      // no exp -> treat as expired
  const mem = new Map();
  const st = makeTokenStore({ getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v), removeItem: (k) => mem.delete(k) });
  st.set(` ${old} `); assert.equal(st.get(), null); assert.equal(mem.size, 0);
  st.set(good); assert.equal(st.get(), good); st.clear(); assert.equal(st.get(), null);
});

test("format: actions by role/status", () => {
  assert.deepEqual(actionsFor("OPEN", "viewer"), []);
  assert.deepEqual(actionsFor("OPEN", "reviewer"), ["start_review", "confirm", "reject"]);
  assert.deepEqual(actionsFor("UNDER_REVIEW", "admin"), ["confirm", "reject"]);
  assert.deepEqual(actionsFor("CONFIRMED", "reviewer"), ["resolve"]);
  assert.deepEqual(actionsFor("RESOLVED", "admin"), []);
  assert.deepEqual(actionsFor("FALSE_POSITIVE", "admin"), []);
});

test("format: describe, timeAgo, errorMessage", () => {
  assert.equal(describeIncident({ event_type: "PPE_VIOLATION", missing_ppe: ["helmet", "vest"] }), "Missing helmet, vest");
  assert.equal(describeIncident({ event_type: "HAZARD_DETECTED", hazard_class: "smoke" }), "Possible smoke");
  assert.equal(describeIncident({ event_type: "RESTRICTED_AREA_ENTRY" }), "Restricted area entry");
  const now = Date.parse("2026-01-01T10:00:00Z");
  assert.equal(timeAgo("2026-01-01T09:59:30Z", now), "30s ago");
  assert.equal(timeAgo("2026-01-01T09:00:00Z", now), "1h ago");
  assert.equal(timeAgo("nope", now), "");
  assert.match(errorMessage({ status: 409 }), /not allowed/);
  assert.match(errorMessage({ status: 403 }), /permission/);
});

test("polling: runs, backs off on error, resets, stops, pauses when hidden", async () => {
  const delays = []; let fire = null, calls = 0, errs = 0, visible = true;
  const setT = (f, d) => { delays.push(d); fire = f; return 1; };
  const fn = async () => { calls++; if (calls === 1 || calls === 2) throw new Error("x"); };
  const stop = startPolling(fn, 1000, { onError: () => errs++, isVisible: () => visible, setT, clearT: () => {} });
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(delays, [2000]);                 // 1st failure -> 1000*2^1
  await fire(); assert.deepEqual(delays, [2000, 4000]);
  await fire(); assert.deepEqual(delays, [2000, 4000, 1000]);   // success resets
  visible = false; const before = calls; await fire(); assert.equal(calls, before);   // hidden: skipped
  stop(); await fire(); assert.equal(delays.length, 4);          // stopped: no reschedule
  assert.equal(errs, 2);
});
