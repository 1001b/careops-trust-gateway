"""Local HS256 tokens for the protected route. Not an external identity provider."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

DEMO_ROLES = ("analyst", "operations", "clinical_admin")
DEFAULT_ISSUER = "careops-local"
DEFAULT_AUDIENCE = "careops-trust-gateway"


class IdentityError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Identity:
    principal: str
    role: str
    tenant: str
    purpose: str
    scopes: tuple[str, ...]
    issuer: str
    audience: str


@dataclass(frozen=True)
class IdentityConfig:
    secret: str
    issuer: str = DEFAULT_ISSUER
    audience: str = DEFAULT_AUDIENCE


def config_from_env() -> IdentityConfig:
    secret = os.environ.get("CAREOPS_JWT_SECRET")
    if not secret:
        raise IdentityError("not_configured")
    return IdentityConfig(
        secret=secret,
        issuer=os.environ.get("CAREOPS_JWT_ISSUER", DEFAULT_ISSUER),
        audience=os.environ.get("CAREOPS_JWT_AUDIENCE", DEFAULT_AUDIENCE),
    )


def bounded_demo_role(role: str) -> Identity:
    """Server-side demo principal. The protected route does not call this."""
    if role not in DEMO_ROLES:
        raise ValueError(f"unknown demo principal: {role}")
    return Identity(
        principal=f"demo-{role}",
        role=role,
        tenant="tenant-demo",
        purpose="operations_analysis",
        scopes=("demo",),
        issuer="careops-demo",
        audience="careops-demo",
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def issue_token(
    config: IdentityConfig,
    *,
    principal: str,
    role: str,
    tenant: str,
    purpose: str = "operations_analysis",
    scopes: list[str] | None = None,
    ttl_seconds: int = 3600,
    now: int | None = None,
    expired: bool = False,
    alg: str = "HS256",
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else int(now)
    expires_at = issued_at - 10 if expired else issued_at + ttl_seconds
    header = {"alg": alg, "typ": "JWT"}
    payload = {
        "iss": config.issuer if issuer is None else issuer,
        "aud": config.audience if audience is None else audience,
        "iat": issued_at,
        "exp": expires_at,
        "sub": principal,
        "principal": principal,
        "role": role,
        "tenant": tenant,
        "purpose": purpose,
        "scopes": ["query"] if scopes is None else scopes,
    }
    signing_input = f"{_b64url(_json_bytes(header))}.{_b64url(_json_bytes(payload))}"
    if alg == "none":
        return f"{signing_input}."
    signature = hmac.new(config.secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def verify_token(token: str, config: IdentityConfig, *, now: int | None = None) -> Identity:
    parts = token.split(".")
    if len(parts) != 3:
        raise IdentityError("malformed")
    try:
        header = json.loads(_b64url_decode(parts[0]))
    except (json.JSONDecodeError, ValueError) as exc:
        raise IdentityError("malformed") from exc
    alg = header.get("alg")
    if alg == "none" or parts[2] == "":
        raise IdentityError("unsigned")
    if alg != "HS256":
        raise IdentityError("forged")
    signing_input = f"{parts[0]}.{parts[1]}"
    expected = _b64url(
        hmac.new(config.secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected, parts[2]):
        raise IdentityError("forged")
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (json.JSONDecodeError, ValueError) as exc:
        raise IdentityError("malformed") from exc
    if payload.get("iss") != config.issuer:
        raise IdentityError("wrong_issuer")
    audience = payload.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if config.audience not in audiences:
        raise IdentityError("wrong_audience")
    try:
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IdentityError("malformed") from exc
    current = int(time.time()) if now is None else int(now)
    if expires_at <= current:
        raise IdentityError("expired")
    scopes = payload.get("scopes") or []
    if not isinstance(scopes, list):
        raise IdentityError("malformed")
    return Identity(
        principal=str(payload.get("principal") or payload.get("sub") or ""),
        role=str(payload.get("role") or ""),
        tenant=str(payload.get("tenant") or ""),
        purpose=str(payload.get("purpose") or ""),
        scopes=tuple(str(item) for item in scopes),
        issuer=str(payload["iss"]),
        audience=str(audience if isinstance(audience, str) else config.audience),
    )
