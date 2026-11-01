"""HTTP clients for internal services (stdlib only). Gateway code depends on the `Upstream` shape, so tests inject fakes."""
from __future__ import annotations
import json
import urllib.error
import urllib.parse
import urllib.request

from .errors import ApiError


class HttpUpstream:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base, self.timeout = base_url.rstrip("/"), timeout

    def request(self, method, path, params=None, body=None, raw=False):
        url = self.base + path + (("?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        try:    # M9: carry the caller's request id so one user action can be followed across services in the logs
            from vg_obs.logs import get_context
            if get_context().get("request_id"):
                headers["X-Request-ID"] = get_context()["request_id"]
        except ImportError:
            pass
        req = urllib.request.Request(url, data, headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = r.read()
                return payload if raw else json.loads(payload or b"null")
        except urllib.error.HTTPError as e:
            # Pass through expected client errors, hide upstream internals for everything else.
            if e.code in (404, 409, 422):
                raise ApiError(e.code, "upstream: " + (json.loads(e.read() or b"{}").get("detail", "error") if e.code != 404 else "not found"))
            raise ApiError(502, "upstream error")
        except Exception:
            raise ApiError(503, "upstream unavailable")

    def get(self, path, **params):
        return self.request("GET", path, params or None)

    def get_raw(self, path, **params):
        return self.request("GET", path, params or None, raw=True)

    def post(self, path, body=None, **params):
        return self.request("POST", path, params or None, body)
