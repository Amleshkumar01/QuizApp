"""
Central role and object-level permission helpers for PlacementIQ.

Roles
-----
Super Admin : Django ``is_superuser=True`` and ``is_staff=True``. Full control.
Teacher     : Django ``is_staff=True``, ``is_superuser=False`` and a member of the
              Django group named :data:`TEACHER_GROUP`.
Student     : ``is_staff=False``. Authenticated through Supabase Auth. Unaffected
              by these helpers.

All permission decisions must be enforced on the backend. Template-level hiding of
links/buttons is only a UX convenience and never a security boundary.
"""
from __future__ import annotations

TEACHER_GROUP = "Teacher"


def is_super_admin(user) -> bool:
    """Return ``True`` only for a fully privileged Super Admin.

    A Super Admin is an authenticated, active Django user that is both a
    superuser and staff. A normal ``is_staff`` user must never be treated as a
    Super Admin.
    """
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and user.is_staff
        and user.is_superuser
    )


def is_teacher(user) -> bool:
    """Return ``True`` for an active Teacher.

    A Teacher is staff, *not* a superuser, and belongs to the ``Teacher`` group.
    Super Admins are intentionally excluded here so callers can distinguish the
    two roles; use :func:`is_staff_member` when either should pass.
    """
    if not (user and user.is_authenticated and user.is_active and user.is_staff):
        return False
    if user.is_superuser:
        return False
    return user.groups.filter(name=TEACHER_GROUP).exists()


def is_staff_member(user) -> bool:
    """Return ``True`` for a Super Admin or a Teacher (any privileged staff)."""
    return is_super_admin(user) or is_teacher(user)


def staff_role(user) -> str:
    """Return a short role label used by templates/context: ``super_admin``,
    ``teacher`` or ``""`` (no staff role)."""
    if is_super_admin(user):
        return "super_admin"
    if is_teacher(user):
        return "teacher"
    return ""


# ---------------------------------------------------------------------------
# Object-level ownership checks
#
# These guard against IDOR: every edit/delete/question route must call the
# relevant helper with the fetched object, never trust the URL alone.
# ---------------------------------------------------------------------------

def _assigned_ids(obj, attr):
    """Safely collect assigned-teacher user ids from a M2M relation that may not
    exist yet (e.g. before the ownership migration has run)."""
    manager = getattr(obj, attr, None)
    if manager is None:
        return set()
    try:
        return set(manager.values_list("id", flat=True))
    except Exception:
        return set()


def can_manage_company(user, company) -> bool:
    """Super Admin manages any company. A Teacher manages a company they created
    or are explicitly assigned to."""
    if is_super_admin(user):
        return True
    if not is_teacher(user):
        return False
    if getattr(company, "created_by_id", None) == user.id:
        return True
    return user.id in _assigned_ids(company, "assigned_teachers")


def can_manage_drive(user, drive) -> bool:
    """Super Admin manages any drive. A Teacher manages a drive they created, are
    assigned to, or whose linked company they can manage."""
    if is_super_admin(user):
        return True
    if not is_teacher(user):
        return False
    if getattr(drive, "created_by_id", None) == user.id:
        return True
    if user.id in _assigned_ids(drive, "assigned_teachers"):
        return True
    company = getattr(drive, "company", None)
    if company is not None and can_manage_company(user, company):
        return True
    return False


def can_manage_quiz(user, quiz) -> bool:
    """Super Admin manages any quiz. A Teacher manages a quiz they created, are
    assigned to, or whose linked drive/company they can manage."""
    if is_super_admin(user):
        return True
    if not is_teacher(user):
        return False
    if getattr(quiz, "created_by_id", None) == user.id:
        return True
    if user.id in _assigned_ids(quiz, "assigned_teachers"):
        return True
    drive = getattr(quiz, "placement_drive", None)
    if drive is not None and can_manage_drive(user, drive):
        return True
    company = getattr(quiz, "company", None)
    if company is not None and can_manage_company(user, company):
        return True
    return False


def can_delete_quiz(user, quiz) -> bool:
    """Whether the user may *attempt* deletion of a quiz.

    Actual behaviour (hard delete vs. archive) is decided by the view based on
    whether the quiz has attempts/imported results. This only checks ownership.
    """
    return can_manage_quiz(user, quiz)
