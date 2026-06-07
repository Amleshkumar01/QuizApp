from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def admin_required(view_func):
    """Only staff users may access admin portal views."""

    @login_required(login_url=settings.ADMIN_LOGIN_URL)
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(request, "Access denied. Admin credentials required.")
            return redirect("student_dashboard")
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
                "Admin accounts cannot access student pages. Use the admin dashboard instead.",
            )
            return redirect("admin_dashboard")
        return view_func(request, *args, **kwargs)

    return wrapper
