"""
Authentication decorators for Supabase-backed views.
"""

import logging
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect

from . import supabase_client as sb

logger = logging.getLogger(__name__)


def supabase_login_required(view_func):
    """Requires a valid Supabase session in Django session."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        supabase_uid = request.session.get("supabase_user_id")
        access_token = request.session.get("supabase_access_token")
        refresh_token = request.session.get("supabase_refresh_token")

        if not supabase_uid or not access_token:
            messages.info(request, "Please sign in to continue.")
            return redirect(settings.LOGIN_URL)

        # Verify the access token
        try:
            user = sb.get_user(access_token)
            if not user:
                raise ValueError("invalid token")
        except Exception:
            # Try refresh
            if refresh_token:
                try:
                    refreshed = sb.refresh_session(refresh_token)
                    if refreshed and refreshed.session:
                        request.session["supabase_access_token"] = refreshed.session.access_token
                        request.session["supabase_refresh_token"] = refreshed.session.refresh_token
                    else:
                        raise ValueError("refresh failed")
                except Exception:
                    request.session.flush()
                    messages.warning(request, "Your session has expired. Please sign in again.")
                    return redirect(settings.LOGIN_URL)
            else:
                request.session.flush()
                messages.warning(request, "Your session has expired. Please sign in again.")
                return redirect(settings.LOGIN_URL)

        request.supabase_user_id = supabase_uid
        request.supabase_role = request.session.get("supabase_role", "student")
        return view_func(request, *args, **kwargs)

    return wrapper


def supabase_student_required(view_func):
    """Requires role=student or role=admin."""
    @wraps(view_func)
    @supabase_login_required
    def wrapper(request, *args, **kwargs):
        role = getattr(request, "supabase_role", "student")
        if role not in ("student", "admin"):
            return HttpResponseForbidden("Access denied.")
        return view_func(request, *args, **kwargs)
    return wrapper


def supabase_admin_required(view_func):
    """Requires role=admin."""
    @wraps(view_func)
    @supabase_login_required
    def wrapper(request, *args, **kwargs):
        role = getattr(request, "supabase_role", "")
        if role != "admin":
            return HttpResponseForbidden("Access denied. Admin privileges required.")
        return view_func(request, *args, **kwargs)
    return wrapper
