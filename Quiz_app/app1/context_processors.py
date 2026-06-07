"""Template context for college-authenticated student profile (session only)."""


def student_profile(request):
    if not request.user.is_authenticated or request.user.is_staff:
        return {}

    enrollment_id = request.session.get("student_enrollment_id") or request.user.username
    from .college_auth import is_valid_student_name

    session_name = request.session.get("student_display_name") or ""
    stored_name = request.user.first_name or ""
    display_name = ""
    if is_valid_student_name(session_name, enrollment_id):
        display_name = session_name
    elif is_valid_student_name(stored_name, enrollment_id):
        display_name = stored_name
    email = request.session.get("student_email") or request.user.email or ""
    nav_label = display_name or enrollment_id

    return {
        "student_enrollment_id": enrollment_id,
        "student_display_name": display_name,
        "student_email": email,
        "student_nav_label": nav_label,
    }
