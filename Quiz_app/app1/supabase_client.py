"""
Supabase client for PlacementIQ.

Uses Python's stdlib urllib.request for all HTTP calls to avoid the
Windows WinError 10013 issue where httpx gets blocked inside Django's
runserver process by firewall/antivirus software.

The supabase SDK (httpx) works in standalone scripts but fails inside
long-running server processes on Windows. This module provides equivalent
functionality using urllib which is never blocked (whitelisted by Windows).
"""

import base64
import hashlib
import json
import logging
import secrets
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_SSL_CTX = ssl.create_default_context()


def generate_pkce_pair():
    """
    Generate a PKCE (code_verifier, code_challenge) pair.
    The verifier is a random string; the challenge is its SHA-256 hash,
    base64url-encoded without padding. Used for the OAuth PKCE flow so
    Supabase returns an authorization code as a server-readable query param.
    """
    verifier = secrets.token_urlsafe(64)[:96]  # 43-128 chars, URL-safe
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return verifier, challenge


class SupabaseError(Exception):
    """Error from Supabase API."""
    def __init__(self, message: str, status: int = 0):
        self.message = message
        self.status = status
        super().__init__(message)


@dataclass
class SupabaseUser:
    id: str
    email: str
    user_metadata: dict
    identities: list


@dataclass
class SupabaseSession:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass
class AuthResponse:
    user: Optional[SupabaseUser]
    session: Optional[SupabaseSession]


def _api_call(method: str, path: str, data: dict = None, key: str = None, token: str = None, timeout: int = 20) -> Any:
    """
    Make a direct HTTP call to the Supabase API using urllib.
    Returns parsed JSON response.
    """
    url = f"{settings.SUPABASE_URL}{path}"
    api_key = key or settings.SUPABASE_PUBLISHABLE_KEY

    headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    body = json.dumps(data).encode("utf-8") if data else None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            resp_body = resp.read().decode("utf-8")
            if resp_body:
                return json.loads(resp_body)
            return None
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        # Try to parse error message from Supabase
        msg = error_body
        try:
            err_json = json.loads(error_body)
            msg = err_json.get("error_description") or err_json.get("msg") or err_json.get("message") or error_body
        except (json.JSONDecodeError, KeyError):
            pass
        raise SupabaseError(msg, status=e.code)
    except urllib.error.URLError as e:
        raise SupabaseError(f"Connection failed: {e.reason}")


# ─── Auth Operations ─────────────────────────────────────────────────────────

def sign_up(email: str, password: str, user_metadata: dict = None) -> AuthResponse:
    """Register a new user via Supabase GoTrue."""
    payload = {
        "email": email,
        "password": password,
    }
    if user_metadata:
        payload["data"] = user_metadata

    result = _api_call("POST", "/auth/v1/signup", data=payload)

    user = None
    session = None
    if result:
        if "id" in result:
            user = SupabaseUser(
                id=result["id"],
                email=result.get("email", email),
                user_metadata=result.get("user_metadata", {}),
                identities=result.get("identities", []),
            )
        elif "user" in result and result["user"]:
            u = result["user"]
            user = SupabaseUser(
                id=u["id"],
                email=u.get("email", email),
                user_metadata=u.get("user_metadata", {}),
                identities=u.get("identities", []),
            )
        if "access_token" in result:
            session = SupabaseSession(
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token", ""),
                expires_in=result.get("expires_in", 3600),
            )
        elif "session" in result and result["session"]:
            s = result["session"]
            session = SupabaseSession(
                access_token=s["access_token"],
                refresh_token=s.get("refresh_token", ""),
                expires_in=s.get("expires_in", 3600),
            )

    return AuthResponse(user=user, session=session)


def sign_in_with_password(email: str, password: str) -> AuthResponse:
    """Authenticate with email + password."""
    payload = {
        "email": email,
        "password": password,
    }
    result = _api_call("POST", "/auth/v1/token?grant_type=password", data=payload)

    user = None
    session = None
    if result:
        if "access_token" in result:
            session = SupabaseSession(
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token", ""),
                expires_in=result.get("expires_in", 3600),
            )
        if "user" in result and result["user"]:
            u = result["user"]
            user = SupabaseUser(
                id=u["id"],
                email=u.get("email", email),
                user_metadata=u.get("user_metadata", {}),
                identities=u.get("identities", []),
            )

    return AuthResponse(user=user, session=session)


def refresh_session(refresh_token: str) -> AuthResponse:
    """Refresh an expired access token."""
    payload = {"refresh_token": refresh_token}
    result = _api_call("POST", "/auth/v1/token?grant_type=refresh_token", data=payload)

    session = None
    user = None
    if result:
        if "access_token" in result:
            session = SupabaseSession(
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token", refresh_token),
                expires_in=result.get("expires_in", 3600),
            )
        if "user" in result and result["user"]:
            u = result["user"]
            user = SupabaseUser(
                id=u["id"],
                email=u.get("email", ""),
                user_metadata=u.get("user_metadata", {}),
                identities=u.get("identities", []),
            )

    return AuthResponse(user=user, session=session)


def get_user(access_token: str) -> Optional[SupabaseUser]:
    """Verify an access token and get user info."""
    result = _api_call("GET", "/auth/v1/user", token=access_token)
    if result and "id" in result:
        return SupabaseUser(
            id=result["id"],
            email=result.get("email", ""),
            user_metadata=result.get("user_metadata", {}),
            identities=result.get("identities", []),
        )
    return None


def admin_get_user_by_id(user_id: str) -> Optional[SupabaseUser]:
    """Get a user by ID using service role key (admin API)."""
    result = _api_call("GET", f"/auth/v1/admin/users/{user_id}", key=settings.SUPABASE_SECRET_KEY)
    if result and "id" in result:
        return SupabaseUser(
            id=result["id"],
            email=result.get("email", ""),
            user_metadata=result.get("user_metadata", {}),
            identities=result.get("identities", []),
        )
    return None


# ─── Database Operations ─────────────────────────────────────────────────────

def rpc(function_name: str, params: dict = None) -> Any:
    """Call a Supabase RPC function using the service role key."""
    return _api_call(
        "POST",
        f"/rest/v1/rpc/{function_name}",
        data=params or {},
        key=settings.SUPABASE_SECRET_KEY,
    )


def table_select(table: str, columns: str = "*", filters: dict = None, limit: int = None) -> list:
    """
    Simple table select using the service role key.
    filters: dict of {column: value} for eq filters.
    """
    params = {"select": columns}
    if limit:
        params["limit"] = str(limit)

    query_string = urllib.parse.urlencode(params)
    path = f"/rest/v1/{table}?{query_string}"

    # Add filter params
    if filters:
        for col, val in filters.items():
            path += f"&{col}=eq.{urllib.parse.quote(str(val))}"

    result = _api_call("GET", path, key=settings.SUPABASE_SECRET_KEY)
    return result if isinstance(result, list) else []


def table_upsert(table: str, row: dict, on_conflict: str = None) -> list:
    """
    Upsert a row using the service role key (bypasses RLS).
    Used to guarantee a profiles row exists for OAuth users.
    """
    path = f"/rest/v1/{table}"
    if on_conflict:
        path += f"?on_conflict={urllib.parse.quote(on_conflict)}"

    url = f"{settings.SUPABASE_URL}{path}"
    headers = {
        "apikey": settings.SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    body = json.dumps(row).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
            resp_body = resp.read().decode("utf-8")
            return json.loads(resp_body) if resp_body else []
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        raise SupabaseError(error_body or str(e), status=e.code)
    except urllib.error.URLError as e:
        raise SupabaseError(f"Connection failed: {e.reason}")


# ─── Password Reset ──────────────────────────────────────────────────────────

def reset_password_for_email(email: str, redirect_to: str = None) -> None:
    """Send a password reset email via Supabase GoTrue."""
    data = {"email": email}
    if redirect_to:
        data["redirect_to"] = redirect_to
    _api_call("POST", "/auth/v1/recover", data=data)


# ─── OAuth ───────────────────────────────────────────────────────────────────

def get_oauth_url(provider: str, redirect_to: str, code_challenge: str = None) -> str:
    """
    Get the Supabase OAuth URL for a given provider (e.g. 'google').

    When code_challenge is provided, uses the PKCE flow: Supabase returns an
    authorization code as a query parameter (?code=) to redirect_to — which is
    server-readable (no JS/hash-fragment needed). This is the reliable flow.

    redirect_to MUST be listed in Supabase Dashboard → Auth → URL Configuration
    → Redirect URLs.
    """
    params = {
        "provider": provider,
        "redirect_to": redirect_to,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "s256"
    return f"{settings.SUPABASE_URL}/auth/v1/authorize?{urllib.parse.urlencode(params)}"


def exchange_code_for_session(code: str, code_verifier: str = None) -> AuthResponse:
    """
    Exchange an OAuth authorization code for a session (PKCE flow).
    code_verifier must match the challenge sent in the authorize request.
    """
    data = {"auth_code": code}
    if code_verifier:
        data["code_verifier"] = code_verifier

    result = _api_call(
        "POST",
        "/auth/v1/token?grant_type=pkce",
        data=data,
    )

    user = None
    session = None
    if result:
        if "access_token" in result:
            session = SupabaseSession(
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token", ""),
                expires_in=result.get("expires_in", 3600),
            )
        if "user" in result and result["user"]:
            u = result["user"]
            user = SupabaseUser(
                id=u["id"],
                email=u.get("email", ""),
                user_metadata=u.get("user_metadata", {}),
                identities=u.get("identities", []),
            )

    return AuthResponse(user=user, session=session)


# ─── Update User Password ────────────────────────────────────────────────────

def update_user_password(access_token: str, new_password: str) -> bool:
    """Update user's password using their access token (from reset link)."""
    result = _api_call(
        "PUT",
        "/auth/v1/user",
        data={"password": new_password},
        token=access_token,
    )
    return result is not None
