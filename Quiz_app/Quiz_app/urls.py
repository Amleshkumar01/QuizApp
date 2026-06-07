"""
URL configuration for student_reg project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from app1 import views

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

    # Block default Django admin login at /admin/ (must be after all other /admin/... routes).
    path("admin/", views.django_admin_blocked, name="django_admin_blocked"),

    # Student portal
    path("student/login/", views.student_login_view, name="student_login"),
    path("student/register/", views.student_register, name="student_register"),
    path("student/dashboard/", views.student_dashboard, name="student_dashboard"),

    # Legacy auth URLs (redirects)
    path("login/", views.login_view, name="login"),
    path("register/", views.register, name="register"),

    path("", views.home, name="home"),
    path("company/<int:company_id>/", views.company_tests, name="company_tests"),
    path("logout/", views.logout_view, name="logout"),
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
