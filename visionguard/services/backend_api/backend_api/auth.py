"""Token verification. HS256 (dev / service-to-service) is stdlib-only. OIDC RS256 uses PyJWT if installed.
Never trust the 'alg' header: the verifier's configured algorithm is the only one accepted."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .errors import ApiError


@dataclass
class Principal:
    sub: str
    role: str
    cameras: Optional[list] = None      # None = all cameras; list = restricted site scope
    claims: dict = field(default_factory=dict)


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


def _principal(c: dict, role_claim: str, valid_roles) -> Principal:
    role = c.get(role_claim)
    if isinstance(role, list):
        role = next((r for r in ("admin", "reviewer", "viewer") if r in role), None)
    if role not in valid_roles:
        raise ApiError(403, "no valid role in token")
    cams = c.get("cameras")
    if cams is not None and not (isinstance(cams, list) and all(isinstance(x, str) for x in cams)):
        raise ApiError(401, "bad cameras claim")
    return Principal(sub=str(c["sub"]), role=role, cameras=cams, claims=c)


class HS256Verifier:
    def __init__(self, secret: str, audience: str, issuer: str, role_claim="role", valid_roles=("viewer", "reviewer", "admin"), leeway=30, clock=time.time):
        if len(secret) < 32:
            raise ValueError("HS256 secret must be >= 32 chars")
        self.secret, self.aud, self.iss = secret.encode(), audience, issuer
        self.role_claim, self.roles, self.leeway, self.clock = role_claim, valid_roles, leeway, clock

    def sign(self, claims: dict) -> str:       # used by tests / dev tooling only
        h = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        p = _b64e(json.dumps(claims).encode())
        sig = hmac.new(self.secret, f"{h}.{p}".encode(), hashlib.sha256).digest()
        return f"{h}.{p}.{_b64e(sig)}"

    def verify(self, token: str) -> Principal:
        try:
            h64, p64, s64 = token.split(".")
            header, claims = json.loads(_b64d(h64)), json.loads(_b64d(p64))
            sig = _b64d(s64)
        except Exception:
            raise ApiError(401, "malformed token")
        if header.get("alg") != "HS256":
            raise ApiError(401, "unsupported algorithm")
        good = hmac.new(self.secret, f"{h64}.{p64}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, good):
            raise ApiError(401, "bad signature")
        now = self.clock()
        if "exp" not in claims or now > float(claims["exp"]) + self.leeway:
            raise ApiError(401, "token expired")
        if "nbf" in claims and now < float(claims["nbf"]) - self.leeway:
            raise ApiError(401, "token not yet valid")
        aud = claims.get("aud")
        if self.aud not in (aud if isinstance(aud, list) else [aud]):
            raise ApiError(401, "wrong audience")
        if claims.get("iss") != self.iss:
            raise ApiError(401, "wrong issuer")
        if not claims.get("sub"):
            raise ApiError(401, "no subject")
        return _principal(claims, self.role_claim, self.roles)


class OidcVerifier:  # pragma: no cover - needs IdP + pyjwt
    def __init__(self, jwks_url, audience, issuer, role_claim="roles", valid_roles=("viewer", "reviewer", "admin")):
        import jwt
        self.jwt, self.client = jwt, jwt.PyJWKClient(jwks_url)
        self.aud, self.iss, self.role_claim, self.roles = audience, issuer, role_claim, valid_roles

    def verify(self, token):
        try:
            key = self.client.get_signing_key_from_jwt(token).key
            c = self.jwt.decode(token, key, algorithms=["RS256"], audience=self.aud, issuer=self.iss,
                                options={"require": ["exp", "sub"]})
        except Exception:
            raise ApiError(401, "invalid token")
        return _principal(c, self.role_claim, self.roles)
