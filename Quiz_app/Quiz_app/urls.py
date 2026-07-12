"""
URL configuration for student_reg project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from app1 import views
from app1 import supabase_auth
from app1 import teacher_views
from app1 import admin_teacher_views

urlpatterns = [
    # Custom admin portal — listed before path('admin/', ...) so routes are not swallowed.
    path("admin/login/", views.admin_login_view, name="admin_login"),
    path("admin/dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("admin/companies/", views.admin_manage_companies, name="admin_manage_companies"),
    path("admin/companies/add/", views.admin_add_company, name="admin_add_company"),
    path("admin/companies/edit/<int:company_id>/", views.admin_edit_company, name="admin_edit_company"),
    path("admin/companies/delete/<int:company_id>/", views.admin_delete_company, name="admin_delete_company"),
    path("admin/test-levels/", views.admin_manage_test_levels, name="admin_manage_test_levels"),
    path("admin/results/", views.admin_manage_results, name="admin_manage_results"),
    path("admin/users/", views.admin_manage_users, name="admin_manage_users"),
    path("admin/users/add/", views.admin_add_user, name="admin_add_user"),
    path("admin/users/edit/<int:user_id>/", views.edit_user, name="edit_user"),
    path("admin/users/upload_csv/", views.upload_users_csv, name="upload_users_csv"),
    path("admin/users/delete/<int:user_id>/", views.delete_user, name="delete_user"),
    path("admin/quizzes/", views.admin_manage_quizzes, name="admin_manage_quizzes"),
    path("admin/quizzes/add/", views.admin_add_quiz, name="admin_add_quiz"),
    path("admin/quizzes/edit/<int:quiz_id>/", views.admin_edit_quiz, name="admin_edit_quiz"),
    path("admin/quizzes/delete/<int:quiz_id>/", views.admin_delete_quiz, name="admin_delete_quiz"),
    path("admin/quizzes/<int:quiz_id>/questions/", views.admin_quiz_questions, name="admin_quiz_questions"),
    path("admin/quizzes/<int:quiz_id>/questions/add/", views.admin_add_question, name="admin_add_question"),
    path("admin/questions/edit/<int:question_id>/", views.admin_edit_question, name="admin_edit_question"),
    path("admin/questions/delete/<int:question_id>/", views.admin_delete_question, name="admin_delete_question"),
    path("admin/quizzes/upload_csv/", views.upload_quizzes_csv, name="upload_quizzes_csv"),
    path("admin/quizzes/<int:quiz_id>/ai-generate/", views.admin_ai_generate_questions, name="admin_ai_generate"),
    path("admin/analytics/", views.admin_analytics, name="admin_analytics"),

    # Admin full control — additional routes
    path("admin/results/delete/<int:attempt_id>/", views.admin_delete_attempt, name="admin_delete_attempt"),
    path("admin/results/export/", views.admin_export_results, name="admin_export_results"),
    path("admin/students/<int:user_id>/", views.admin_student_detail, name="admin_student_detail"),
    path("admin/students/<int:user_id>/reset-password/", views.admin_reset_password, name="admin_reset_password"),
    path("admin/categories/", views.admin_manage_categories, name="admin_manage_categories"),
    path("admin/categories/add/", views.admin_add_category, name="admin_add_category"),
    path("admin/categories/edit/<int:category_id>/", views.admin_edit_category, name="admin_edit_category"),
    path("admin/categories/delete/<int:category_id>/", views.admin_delete_category, name="admin_delete_category"),
    path("admin/users/bulk-delete/", views.admin_bulk_delete_users, name="admin_bulk_delete_users"),
    path("admin/quizzes/bulk-status/", views.admin_bulk_quiz_status, name="admin_bulk_quiz_status"),
    path("admin/settings/", views.admin_site_settings, name="admin_site_settings"),

    # Super Admin — Teacher management & audit logs
    path("admin/teachers/", admin_teacher_views.admin_teachers, name="admin_teachers"),
    path("admin/teachers/add/", admin_teacher_views.admin_add_teacher, name="admin_add_teacher"),
    path("admin/teachers/<int:teacher_id>/edit/", admin_teacher_views.admin_edit_teacher, name="admin_edit_teacher"),
    path("admin/teachers/<int:teacher_id>/deactivate/", admin_teacher_views.admin_deactivate_teacher, name="admin_deactivate_teacher"),
    path("admin/teachers/<int:teacher_id>/reset-password/", admin_teacher_views.admin_reset_teacher_password, name="admin_reset_teacher_password"),
    path("admin/audit-logs/", admin_teacher_views.admin_audit_logs, name="admin_audit_logs"),

    # Teacher portal
    path("teacher/dashboard/", teacher_views.teacher_dashboard, name="teacher_dashboard"),
    path("teacher/companies/", teacher_views.teacher_companies, name="teacher_companies"),
    path("teacher/companies/add/", teacher_views.teacher_add_company, name="teacher_add_company"),
    path("teacher/companies/<int:company_id>/edit/", teacher_views.teacher_edit_company, name="teacher_edit_company"),
    path("teacher/companies/<int:company_id>/toggle/", teacher_views.teacher_toggle_company, name="teacher_toggle_company"),
    path("teacher/drives/", teacher_views.teacher_drives, name="teacher_drives"),
    path("teacher/drives/add/", teacher_views.teacher_add_drive, name="teacher_add_drive"),
    path("teacher/drives/<int:drive_id>/edit/", teacher_views.teacher_edit_drive, name="teacher_edit_drive"),
    path("teacher/quizzes/", teacher_views.teacher_quizzes, name="teacher_quizzes"),
    path("teacher/quizzes/add/", teacher_views.teacher_add_quiz, name="teacher_add_quiz"),
    path("teacher/quizzes/<int:quiz_id>/edit/", teacher_views.teacher_edit_quiz, name="teacher_edit_quiz"),
    path("teacher/quizzes/<int:quiz_id>/delete/", teacher_views.teacher_delete_quiz, name="teacher_delete_quiz"),
    path("teacher/quizzes/<int:quiz_id>/questions/", teacher_views.teacher_quiz_questions, name="teacher_quiz_questions"),
    path("teacher/quizzes/<int:quiz_id>/questions/add/", teacher_views.teacher_add_question, name="teacher_add_question"),
    path("teacher/questions/<int:question_id>/edit/", teacher_views.teacher_edit_question, name="teacher_edit_question"),
    path("teacher/questions/<int:question_id>/delete/", teacher_views.teacher_delete_question, name="teacher_delete_question"),
    path("teacher/quizzes/<int:quiz_id>/upload-csv/", teacher_views.teacher_upload_csv, name="teacher_upload_csv"),
    path("teacher/quizzes/<int:quiz_id>/ai-generate/", teacher_views.teacher_ai_generate, name="teacher_ai_generate"),
    path("teacher/students/", teacher_views.teacher_students, name="teacher_students"),
    path("teacher/students/import/", teacher_views.teacher_import_students, name="teacher_import_students"),
    path("teacher/students/export/", teacher_views.teacher_export_students, name="teacher_export_students"),
    path("teacher/students/<int:user_id>/", teacher_views.teacher_student_detail, name="teacher_student_detail"),
    path("teacher/students/<int:user_id>/edit/", teacher_views.teacher_student_edit, name="teacher_student_edit"),
    path("teacher/results/", teacher_views.teacher_results, name="teacher_results"),
    path("teacher/results/import/", teacher_views.teacher_import_results, name="teacher_import_results"),
    path("teacher/results/export/", teacher_views.teacher_export_results, name="teacher_export_results"),
    path("teacher/analytics/", teacher_views.teacher_analytics, name="teacher_analytics"),
    path("teacher/import-history/", teacher_views.teacher_import_history, name="teacher_import_history"),

    # Block default Django admin login at /admin/ (must be after all other /admin/... routes).
    path("admin/", views.django_admin_blocked, name="django_admin_blocked"),

    # Student portal (Supabase Auth)
    path("student/login/", supabase_auth.supabase_login_view, name="student_login"),
    path("student/register/", supabase_auth.supabase_register_view, name="student_register"),
    path("student/forgot-password/", supabase_auth.supabase_forgot_password_view, name="forgot_password"),
    path("student/reset-password/", supabase_auth.supabase_reset_password_view, name="reset_password"),
    path("student/dashboard/", views.student_dashboard, name="student_dashboard"),

    # OAuth
    path("auth/google/", supabase_auth.supabase_google_login_view, name="google_login"),
    path("auth/google/signup/", supabase_auth.supabase_google_signup_view, name="google_signup"),
    path("auth/google/callback/", supabase_auth.supabase_google_callback_view, name="google_callback"),

    # Legacy auth URLs (redirects)
    path("login/", views.login_view, name="login"),
    path("register/", views.register, name="register"),
    path("signup/", supabase_auth.supabase_register_view, name="signup"),

    path("", views.home, name="home"),
    path("company/<int:company_id>/", views.company_tests, name="company_tests"),
    path("logout/", supabase_auth.supabase_logout_view, name="logout"),
    path("category/<int:category_id>/", views.category_quizzes, name="category_quizzes"),
    path("quiz/<int:quiz_id>/start/", views.start_quiz, name="start_quiz"),
    path("quiz/attempt/<int:quiz_id>/", views.attempt_quiz, name="attempt_quiz"),
    path("quiz/finalize/", views.finalize_quiz, name="finalize_quiz"),
    path("quiz/done/<int:attempt_id>/", views.quiz_result, name="quiz_result"),
    path("my-attempts/", views.my_attempts, name="my_attempts"),
    path("my-analytics/", views.student_analytics, name="student_analytics"),
    path("ai-practice/", views.ai_practice_plan, name="ai_practice_plan"),
]

# Optional hidden Django admin for emergency superuser access only (set DJANGO_INTERNAL_ADMIN_PATH).
if settings.ADMIN_URL:
    from django.contrib import admin

    urlpatterns.append(path(f"{settings.ADMIN_URL.strip('/')}/", admin.site.urls))

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
