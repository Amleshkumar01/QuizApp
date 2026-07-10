"""Template context for authenticated student profile."""


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
