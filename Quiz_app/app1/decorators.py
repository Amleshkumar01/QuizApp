"""
Access-control decorators for PlacementIQ portals.

Role semantics (see :mod:`app1.permissions`):
    superadmin_required : Super Admin only.
    teacher_required    : Teacher or Super Admin.
    staff_required      : Teacher or Super Admin (any privileged staff).
    student_required    : non-staff student accounts only.

``admin_required`` is kept as a backward-compatible alias for
``superadmin_required`` so existing admin views remain Super-Admin-only.
"""
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from .permissions import is_super_admin, is_staff_member, is_teacher


def _staff_login_redirect(request, view_func):
    """Send a logged-in-but-unauthorized user somewhere sensible."""
    if request.user.is_authenticated and request.user.is_staff:
        # A teacher hitting a super-admin-only page, etc.
        if is_teacher(request.user):
            return redirect("teacher_dashboard")
        if is_super_admin(request.user):
            return redirect("admin_dashboard")
    return redirect("student_dashboard")


def superadmin_required(view_func):
    """Only Super Admins (superuser + staff) may access the view."""

    @login_required(login_url=settings.ADMIN_LOGIN_URL)
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not is_super_admin(request.user):
            messages.error(request, "Access denied. Super Admin privileges required.")
            return _staff_login_redirect(request, view_func)
        return view_func(request, *args, **kwargs)

    return wrapper


# Backward-compatible alias: existing admin views become Super-Admin-only.
admin_required = superadmin_required


def teacher_required(view_func):
    """Teachers or Super Admins may access the view."""

    @login_required(login_url=settings.ADMIN_LOGIN_URL)
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not is_staff_member(request.user):
            messages.error(request, "Access denied. Teacher privileges required.")
            return _staff_login_redirect(request, view_func)
        return view_func(request, *args, **kwargs)

    return wrapper


def staff_required(view_func):
    """Any privileged staff (Teacher or Super Admin) may access the view."""

    @login_required(login_url=settings.ADMIN_LOGIN_URL)
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not is_staff_member(request.user):
            messages.error(request, "Access denied. Staff privileges required.")
            return _staff_login_redirect(request, view_func)
        return view_func(request, *args, **kwargs)

    return wrapper


def student_required(view_func):
    """Only non-staff (student) users may access student portal views."""

    @login_required(login_url=settings.LOGIN_URL)
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_staff:
            messages.info(
                request,
                "Staff accounts cannot access student pages. Use your dashboard instead.",
            )
            if is_teacher(request.user):
                return redirect("teacher_dashboard")
            return redirect("admin_dashboard")
        return view_func(request, *args, **kwargs)

    return wrapper
