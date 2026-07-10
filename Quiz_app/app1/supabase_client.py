"""
Supabase client for PlacementIQ.

Uses Python's stdlib urllib.request for all HTTP calls to avoid the
Windows WinError 10013 issue where httpx gets blocked inside Django's
runserver process by firewall/antivirus software.

The supabase SDK (httpx) works in standalone scripts but fails inside
long-running server processes on Windows. This module provides equivalent
functionality using urllib which is never blocked (whitelisted by Windows).
"""

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_SSL_CTX = ssl.create_default_context()


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
