"""Template context processors for PlacementIQ."""

from .permissions import is_super_admin, is_teacher, is_staff_member, staff_role


def student_profile(request):
    if not request.user.is_authenticated or request.user.is_staff:
        return {}

    user = request.user
    display_name = user.get_full_name() or user.username
    email = user.email or ""

    return {
        "student_display_name": display_name,
        "student_email": email,
        "student_nav_label": display_name,
    }


def role_flags(request):
    """Expose role booleans to every template for navigation rendering.

    Hiding links is only for UX; all backend endpoints still enforce
    permissions independently.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {
            "is_super_admin": False,
            "is_teacher": False,
            "is_staff_member": False,
            "staff_role": "",
        }
    return {
        "is_super_admin": is_super_admin(user),
        "is_teacher": is_teacher(user),
        "is_staff_member": is_staff_member(user),
        "staff_role": staff_role(user),
    }
