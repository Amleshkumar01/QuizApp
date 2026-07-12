"""
Shared services for PlacementIQ teacher/admin features:
audit logging, CSV safety helpers, and pending-student claiming.
"""
from __future__ import annotations

import csv
from io import StringIO

from django.utils import timezone

from .models import AuditLog, PendingStudentProfile, StudentProfile


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def get_client_ip(request):
    """Best-effort client IP extraction, respecting a single proxy hop."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_action(request, action, *, model_name="", object_id="", description="", metadata=None):
    """Record a sensitive management action.

    Never pass passwords, tokens, secret keys or raw auth codes in ``metadata``
    or ``description`` — audit logs must stay free of secrets.
    """
    user = getattr(request, "user", None)
    if user is not None and not user.is_authenticated:
        user = None
    try:
        return AuditLog.objects.create(
            user=user,
            action=action[:100],
            model_name=str(model_name)[:100],
            object_id=str(object_id)[:100],
            description=description or "",
            ip_address=get_client_ip(request),
            metadata=metadata or {},
        )
    except Exception:
        # Audit logging must never break the primary request flow.
        return None


# ---------------------------------------------------------------------------
# CSV safety
# ---------------------------------------------------------------------------

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def csv_safe(value):
    """Escape a cell to prevent spreadsheet formula injection.

    Any value beginning with =, +, - or @ is prefixed with a single quote so
    spreadsheet software treats it as text rather than a formula.
    """
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in _FORMULA_PREFIXES:
        return "'" + text
    return text


def write_csv_row(writer, row):
    """Write a row of already-stringifiable values with formula escaping."""
    writer.writerow([csv_safe(v) for v in row])


def build_csv_response(filename, header, rows):
    """Return an HttpResponse containing a CSV with escaped cells."""
    from django.http import HttpResponse

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow([csv_safe(h) for h in header])
    for row in rows:
        write_csv_row(writer, row)
    return response


def build_error_csv(header, error_rows):
    """Return CSV text (string) describing failed import rows."""
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(list(header) + ["error"])
    for row, error in error_rows:
        writer.writerow([csv_safe(v) for v in row] + [csv_safe(error)])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pending student claiming
# ---------------------------------------------------------------------------

def normalize_email(email):
    return (email or "").strip().lower()


def claim_pending_student(user):
    """When a student logs in (Supabase/Google), attach any imported pending
    profile data by normalized email, then mark it claimed.

    Safe to call on every student login; it is a no-op when there is nothing
    to claim. Returns the StudentProfile (created if needed) or None.
    """
    if user is None or not user.email:
        return None
    email = normalize_email(user.email)
    if not email:
        return None

    profile, _ = StudentProfile.objects.get_or_create(user=user)

    pending = (
        PendingStudentProfile.objects
        .filter(email__iexact=email, claimed_by__isnull=True)
        .first()
    )
    if pending is None:
        return profile

    # Transfer pending metadata without overwriting values already set.
    if pending.first_name and not user.first_name:
        user.first_name = pending.first_name
    if pending.last_name and not user.last_name:
        user.last_name = pending.last_name
    user.save(update_fields=["first_name", "last_name"])

    for field in ("roll_number", "college", "branch", "batch", "phone"):
        val = getattr(pending, field)
        if val and not getattr(profile, field):
            setattr(profile, field, val)
    if pending.semester and not profile.semester:
        profile.semester = pending.semester
    try:
        profile.save()
    except Exception:
        # roll_number unique clash etc. — keep profile without pending roll.
        profile.roll_number = None
        profile.save()

    pending.claimed_by = user
    pending.claimed_at = timezone.now()
    pending.save(update_fields=["claimed_by", "claimed_at"])
    return profile
