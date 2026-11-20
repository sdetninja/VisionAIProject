"""Client for the M3 incident service (internal network, no auth: M6 is the only public entry point)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class M3Error(Exception):
    def __init__(self, status, msg):
        super().__init__(f"M3 {status}: {msg}")
        self.status = status


class HttpM3:
    def __init__(self, base_url="http://localhost:8003", timeout=10.0):
        self.base, self.timeout = base_url.rstrip("/"), timeout

    def _call(self, method, path, params=None, body=None, raw=False):
        url = self.base + path + ("?" + urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
                                  if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                b = r.read()
                return b if raw else json.loads(b)
        except urllib.error.HTTPError as e:
            raise M3Error(e.code, e.read()[:200].decode(errors="replace"))
        except (urllib.error.URLError, TimeoutError) as e:
            raise M3Error(503, str(e))

    def incident(self, iid):
        return self._call("GET", f"/incidents/{iid}")

    def evidence(self, uri) -> bytes:
        return self._call("GET", "/evidence", {"uri": uri}, raw=True)

    def incidents(self, **params):
        return self._call("GET", "/incidents", params)

    def post_assessment(self, iid, a):
        return self._call("POST", f"/incidents/{iid}/assessment", body=a)


class FakeM3:
    """In-memory stand-in for tests: incidents {iid: detail}, images {uri: jpeg}."""
    def __init__(self, incidents=None, images=None):
        self.inc, self.images, self.posted = dict(incidents or {}), dict(images or {}), []

    def incident(self, iid):
        if iid not in self.inc:
            raise M3Error(404, "incident not found")
        return {**self.inc[iid], "assessment": next((a for i, a in reversed(self.posted) if i == iid), None)}

    def evidence(self, uri):
        return self.images[uri]

    def incidents(self, **params):
        items = [i for i in self.inc.values() if all(i.get(k) == v for k, v in params.items() if k in ("camera_id", "zone"))]
        return {"total": len(items), "items": items}

    def post_assessment(self, iid, a):
        self.posted.append((iid, a))
        return {"incident_id": iid, "current": True}
