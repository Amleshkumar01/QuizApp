"""
Tests for the Supabase-PostgreSQL migration and the shadow-user / auth wiring.

These tests are network-free: they exercise the Django-side logic (database
configuration parsing, shadow-user creation/mapping, role enforcement, admin
access control, and server-side score calculation). Supabase network calls are
NOT invoked here — only the local Django behaviour that must keep working after
switching the database backend.

Run:  python manage.py test app1
"""

from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import Client, RequestFactory, TestCase

from app1 import supabase_auth as sa
from app1 import supabase_client as sb
from app1.models import (
    Answer,
    Attempt,
    Category,
    Option,
    Question,
    Quiz,
    SupabaseUserMapping,
)


# ─────────────────────────────────────────────────────────────────────────────
# Database configuration
# ─────────────────────────────────────────────────────────────────────────────
class DatabaseConfigTests(TestCase):
    """DATABASE_URL parsing must produce a secure, pooled Postgres config."""

    def test_postgres_url_parses_with_ssl_and_pooling(self):
        import dj_database_url

        sample = (
            "postgresql://postgres.abcdefref:secretpw@aws-0-us-east-1."
            "pooler.supabase.com:5432/postgres"
        )
        cfg = dj_database_url.parse(
            sample, conn_max_age=60, conn_health_checks=True, ssl_require=True
        )
        self.assertEqual(cfg["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(cfg["NAME"], "postgres")
        self.assertEqual(cfg["PORT"], 5432)
        self.assertEqual(cfg["CONN_MAX_AGE"], 60)
        self.assertTrue(cfg.get("CONN_HEALTH_CHECKS"))
        self.assertEqual(cfg.get("OPTIONS", {}).get("sslmode"), "require")

    def test_settings_expose_database_url_variable(self):
        # settings.py must read DATABASE_URL (may be empty in dev), and never
        # crash importing it.
        from django.conf import settings
        self.assertTrue(hasattr(settings, "DATABASE_URL"))


# ─────────────────────────────────────────────────────────────────────────────
# public.profiles must NOT be managed by Django
# ─────────────────────────────────────────────────────────────────────────────
class ProfilesUnmanagedTests(TestCase):
    def test_no_django_model_manages_profiles_table(self):
        for model in apps.get_models():
            self.assertNotEqual(
                model._meta.db_table,
                "profiles",
                msg=f"{model.__name__} must not map to the Supabase 'profiles' table.",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Shadow user creation / lookup / mapping
# ─────────────────────────────────────────────────────────────────────────────
class ShadowUserTests(TestCase):
    def test_creates_shadow_user_and_mapping(self):
        uid = "11111111-1111-1111-1111-111111111111"
        user = sa._get_or_create_shadow_user(
            supabase_user_id=uid,
            username="alice",
            email="alice@example.com",
            first_name="Alice",
            last_name="A",
        )
        self.assertEqual(user.username, "alice")
        self.assertEqual(user.email, "alice@example.com")
        # Student shadow users must never be staff/superuser.
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        # Supabase password is never stored in Django.
        self.assertFalse(user.has_usable_password())
        # Mapping links the verified Supabase UUID.
        mapping = SupabaseUserMapping.objects.get(supabase_user_id=uid)
        self.assertEqual(mapping.user_id, user.pk)

    def test_existing_mapping_is_reused(self):
        uid = "22222222-2222-2222-2222-222222222222"
        u1 = sa._get_or_create_shadow_user(uid, "bob", "bob@example.com")
        u2 = sa._get_or_create_shadow_user(uid, "bob", "bob@example.com")
        self.assertEqual(u1.pk, u2.pk)
        self.assertEqual(User.objects.filter(username="bob").count(), 1)
        self.assertEqual(SupabaseUserMapping.objects.filter(supabase_user_id=uid).count(), 1)

    def test_duplicate_username_is_made_unique(self):
        sa._get_or_create_shadow_user(
            "33333333-3333-3333-3333-333333333333", "carol", "carol1@example.com"
        )
        u2 = sa._get_or_create_shadow_user(
            "44444444-4444-4444-4444-444444444444", "carol", "carol2@example.com"
        )
        self.assertNotEqual(u2.username, "carol")
        self.assertEqual(User.objects.filter(username="carol").count(), 1)
        self.assertEqual(SupabaseUserMapping.objects.count(), 2)

    def test_duplicate_email_across_two_supabase_ids_is_safe(self):
        u1 = sa._get_or_create_shadow_user(
            "55555555-5555-5555-5555-555555555555", "dave", "shared@example.com"
        )
        u2 = sa._get_or_create_shadow_user(
            "66666666-6666-6666-6666-666666666666", "dave2", "shared@example.com"
        )
        self.assertNotEqual(u1.pk, u2.pk)
        self.assertEqual(SupabaseUserMapping.objects.count(), 2)

    def test_mapping_updates_email_on_relogin(self):
        uid = "77777777-7777-7777-7777-777777777777"
        sa._get_or_create_shadow_user(uid, "erin", "old@example.com")
        user = sa._get_or_create_shadow_user(uid, "erin", "new@example.com")
        self.assertEqual(user.email, "new@example.com")


# ─────────────────────────────────────────────────────────────────────────────
# Role enforcement via the login helper
# ─────────────────────────────────────────────────────────────────────────────
class RoleEnforcementTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self):
        request = self.factory.post("/student/login/")
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _session(self):
        return sb.SupabaseSession(
            access_token="test-access", refresh_token="test-refresh", expires_in=3600
        )

    def test_student_role_never_becomes_staff(self):
        request = self._request()
        profile = {"username": "stud1", "first_name": "Stu", "last_name": "One", "role": "student"}
        sa._login_user_from_supabase(
            request, "88888888-8888-8888-8888-888888888888", self._session(),
            profile, "stud1@example.com",
        )
        user = SupabaseUserMapping.objects.get(
            supabase_user_id="88888888-8888-8888-8888-888888888888"
        ).user
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_admin_role_maps_to_staff_only(self):
        # role='admin' comes from the server-verified profiles table (never the
        # browser). It grants staff, but must never grant superuser.
        request = self._request()
        profile = {"username": "adm1", "first_name": "Ad", "last_name": "Min", "role": "admin"}
        sa._login_user_from_supabase(
            request, "99999999-9999-9999-9999-999999999999", self._session(),
            profile, "adm1@example.com",
        )
        user = SupabaseUserMapping.objects.get(
            supabase_user_id="99999999-9999-9999-9999-999999999999"
        ).user
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)


# ─────────────────────────────────────────────────────────────────────────────
# Admin access control
# ─────────────────────────────────────────────────────────────────────────────
class AdminAccessControlTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_anonymous_redirected_from_admin_dashboard(self):
        resp = self.client.get("/admin/dashboard/")
        self.assertEqual(resp.status_code, 302)

    def test_student_cannot_access_admin_dashboard(self):
        student = User.objects.create_user("studx", "studx@example.com", "x")
        student.is_staff = False
        student.save()
        self.client.force_login(student)
        resp = self.client.get("/admin/dashboard/")
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("/admin/dashboard/", resp.url)

    def test_superadmin_can_access_admin_dashboard(self):
        # The admin dashboard is Super-Admin-only (superuser + staff).
        boss = User.objects.create_superuser("adminx", "adminx@example.com", "x")
        self.client.force_login(boss)
        resp = self.client.get("/admin/dashboard/")
        self.assertEqual(resp.status_code, 200)

    def test_plain_staff_cannot_access_admin_dashboard(self):
        # A non-superuser staff account must NOT get Super Admin access.
        staff = User.objects.create_user("adminx2", "adminx2@example.com", "x")
        staff.is_staff = True
        staff.save()
        self.client.force_login(staff)
        resp = self.client.get("/admin/dashboard/")
        self.assertEqual(resp.status_code, 302)


# ─────────────────────────────────────────────────────────────────────────────
# Server-side score calculation & result persistence
# ─────────────────────────────────────────────────────────────────────────────
class ScoreCalculationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user("scorer", "scorer@example.com", "x")
        self.category = Category.objects.create(name="Aptitude")
        self.quiz = Quiz.objects.create(
            title="Sample",
            category=self.category,
            marks_per_question=Decimal("1.00"),
            negative_marks=Decimal("0.25"),
            target_question_count=2,
        )
        self.q1 = Question.objects.create(quiz=self.quiz, text="Q1")
        self.q1_correct = Option.objects.create(question=self.q1, text="right", is_correct=True)
        self.q1_wrong = Option.objects.create(question=self.q1, text="wrong", is_correct=False)
        self.q2 = Question.objects.create(quiz=self.quiz, text="Q2")
        self.q2_correct = Option.objects.create(question=self.q2, text="right", is_correct=True)
        self.q2_wrong = Option.objects.create(question=self.q2, text="wrong", is_correct=False)

    def _request_with_answers(self, answers):
        request = self.factory.post("/quiz/")
        SessionMiddleware(lambda r: None).process_request(request)
        request.user = self.user
        request.session["question_ids"] = [self.q1.id, self.q2.id]
        request.session["answers"] = answers
        request.session["quiz_started_at"] = None
        request.session.save()
        return request

    def test_score_computed_from_db_not_browser(self):
        from app1.views import _persist_quiz_attempt

        # One correct (q1), one wrong (q2). The browser only sends option IDs.
        answers = {str(self.q1.id): self.q1_correct.id, str(self.q2.id): self.q2_wrong.id}
        request = self._request_with_answers(answers)
        attempt = _persist_quiz_attempt(request, self.quiz)

        self.assertEqual(attempt.score, 1)
        self.assertEqual(attempt.correct_count, 1)
        self.assertEqual(attempt.wrong_count, 1)
        # 1 correct (+1.00) − 1 wrong (−0.25) = 0.75, computed server-side.
        self.assertEqual(attempt.marks_obtained, Decimal("0.75"))
        self.assertEqual(attempt.total, 2)

    def test_answers_persisted_with_server_side_correctness(self):
        from app1.views import _persist_quiz_attempt

        answers = {str(self.q1.id): self.q1_wrong.id, str(self.q2.id): self.q2_correct.id}
        request = self._request_with_answers(answers)
        attempt = _persist_quiz_attempt(request, self.quiz)

        a1 = Answer.objects.get(attempt=attempt, question=self.q1)
        a2 = Answer.objects.get(attempt=attempt, question=self.q2)
        # is_correct is derived from Option.is_correct, never trusted from client.
        self.assertFalse(a1.is_correct)
        self.assertTrue(a2.is_correct)
        self.assertEqual(attempt.score, 1)

    def test_attempt_history_persists(self):
        from app1.views import _persist_quiz_attempt

        answers = {str(self.q1.id): self.q1_correct.id, str(self.q2.id): self.q2_correct.id}
        request = self._request_with_answers(answers)
        _persist_quiz_attempt(request, self.quiz)
        self.assertEqual(Attempt.objects.filter(user=self.user, quiz=self.quiz).count(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Teacher Management System
# ─────────────────────────────────────────────────────────────────────────────
from django.contrib.auth.models import Group  # noqa: E402
from django.core.management import call_command  # noqa: E402

from app1.models import (  # noqa: E402
    AuditLog,
    Company,
    ImportedResult,
    PendingStudentProfile,
    PlacementDrive,
    StudentProfile,
    TeacherProfile,
)
from app1.permissions import (  # noqa: E402
    TEACHER_GROUP,
    can_manage_quiz,
    is_staff_member,
    is_super_admin,
    is_teacher,
)
from app1.services import csv_safe  # noqa: E402


def _make_teacher(username="teach1", employee_id="E1"):
    group, _ = Group.objects.get_or_create(name=TEACHER_GROUP)
    user = User.objects.create_user(username=username, email=f"{username}@ex.com",
                                    password="Str0ngPass!42")
    user.is_staff = True
    user.is_superuser = False
    user.save()
    user.groups.add(group)
    TeacherProfile.objects.create(user=user, employee_id=employee_id)
    return user


def _make_superadmin(username="boss"):
    return User.objects.create_superuser(username=username, email="boss@ex.com",
                                         password="Str0ngPass!42")


def _make_student(username="stud1"):
    return User.objects.create_user(username=username, email=f"{username}@ex.com",
                                    password="Str0ngPass!42", is_staff=False)


class RoleHelperTests(TestCase):
    def test_role_helpers(self):
        teacher = _make_teacher()
        boss = _make_superadmin()
        student = _make_student()
        self.assertTrue(is_teacher(teacher))
        self.assertFalse(is_super_admin(teacher))
        self.assertTrue(is_super_admin(boss))
        # Super admin is a staff member but not a "teacher" (group role).
        self.assertTrue(is_staff_member(boss))
        self.assertFalse(is_teacher(boss))
        self.assertFalse(is_staff_member(student))

    def test_plain_staff_is_not_superadmin(self):
        u = User.objects.create_user("plainstaff", "p@ex.com", "Str0ngPass!42")
        u.is_staff = True
        u.save()
        self.assertFalse(is_super_admin(u))
        self.assertFalse(is_teacher(u))  # not in Teacher group


class LoginRedirectTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_teacher_login_redirects_to_teacher_dashboard(self):
        _make_teacher()
        resp = self.client.post("/admin/login/",
                                {"username": "teach1", "password": "Str0ngPass!42"})
        self.assertRedirects(resp, "/teacher/dashboard/", fetch_redirect_response=False)

    def test_superadmin_login_redirects_to_admin_dashboard(self):
        _make_superadmin()
        resp = self.client.post("/admin/login/",
                                {"username": "boss", "password": "Str0ngPass!42"})
        self.assertRedirects(resp, "/admin/dashboard/", fetch_redirect_response=False)

    def test_teacher_login_page_allows_teacher(self):
        _make_teacher()
        resp = self.client.post("/teacher/login/",
                                {"username": "teach1", "password": "Str0ngPass!42"})
        self.assertRedirects(resp, "/teacher/dashboard/", fetch_redirect_response=False)

    def test_teacher_login_page_blocks_superadmin(self):
        _make_superadmin()
        resp = self.client.post("/teacher/login/",
                                {"username": "boss", "password": "Str0ngPass!42"})
        # Should NOT redirect — stays on login page with error
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Super Admin", resp.content)

    def test_teacher_login_page_renders(self):
        resp = self.client.get("/teacher/login/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Teacher Portal", resp.content)


class AccessControlTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_teacher_can_open_dashboard(self):
        t = _make_teacher()
        self.client.force_login(t)
        self.assertEqual(self.client.get("/teacher/dashboard/").status_code, 200)

    def test_student_cannot_open_teacher_dashboard(self):
        s = _make_student()
        self.client.force_login(s)
        resp = self.client.get("/teacher/dashboard/")
        self.assertNotEqual(resp.status_code, 200)  # redirected away

    def test_teacher_cannot_open_superadmin_teacher_management(self):
        t = _make_teacher()
        self.client.force_login(t)
        resp = self.client.get("/admin/teachers/")
        self.assertEqual(resp.status_code, 302)  # blocked by superadmin_required

    def test_all_teacher_pages_render(self):
        """Smoke-test every teacher list/form page renders without template errors."""
        cat = Category.objects.create(name="Apt")
        t = _make_teacher()
        company = Company.objects.create(name="Acme", created_by=t)
        drive = PlacementDrive.objects.create(company=company, title="D",
                                               drive_date="2025-06-01", created_by=t)
        quiz = Quiz.objects.create(title="Q", category=cat, created_by=t)
        Question.objects.create(quiz=quiz, text="Q1")
        self.client.force_login(t)
        pages = [
            "/teacher/dashboard/", "/teacher/companies/", "/teacher/companies/add/",
            f"/teacher/companies/{company.id}/edit/", "/teacher/drives/",
            "/teacher/drives/add/", f"/teacher/drives/{drive.id}/edit/",
            "/teacher/quizzes/", "/teacher/quizzes/add/",
            f"/teacher/quizzes/{quiz.id}/edit/", f"/teacher/quizzes/{quiz.id}/questions/",
            f"/teacher/quizzes/{quiz.id}/questions/add/",
            f"/teacher/quizzes/{quiz.id}/upload-csv/",
            f"/teacher/quizzes/{quiz.id}/ai-generate/",
            "/teacher/students/", f"/teacher/students/{_make_student('sview').id}/",
            "/teacher/results/", "/teacher/results/import/",
            "/teacher/students/import/", "/teacher/analytics/",
            "/teacher/import-history/",
        ]
        for url in pages:
            self.assertEqual(self.client.get(url).status_code, 200, f"{url} did not render")

    def test_superadmin_pages_render(self):
        boss = _make_superadmin("boss2")
        t = _make_teacher("tt", "EE1")
        self.client.force_login(boss)
        for url in ["/admin/teachers/", "/admin/teachers/add/",
                    f"/admin/teachers/{t.teacher_profile.id}/edit/", "/admin/audit-logs/"]:
            self.assertEqual(self.client.get(url).status_code, 200, f"{url} did not render")


class QuizOwnershipTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cat = Category.objects.create(name="Apt")
        self.t1 = _make_teacher("t1", "E1")
        self.t2 = _make_teacher("t2", "E2")

    def _make_quiz(self, owner):
        return Quiz.objects.create(title="Q", category=self.cat, created_by=owner)

    def test_owner_can_manage_other_cannot(self):
        q = self._make_quiz(self.t1)
        self.assertTrue(can_manage_quiz(self.t1, q))
        self.assertFalse(can_manage_quiz(self.t2, q))

    def test_teacher_cannot_edit_others_quiz_via_url(self):
        q = self._make_quiz(self.t1)
        self.client.force_login(self.t2)
        resp = self.client.get(f"/teacher/quizzes/{q.id}/edit/")
        self.assertEqual(resp.status_code, 403)

    def test_delete_requires_post(self):
        q = self._make_quiz(self.t1)
        self.client.force_login(self.t1)
        # GET on a POST-only delete route is rejected.
        self.assertEqual(self.client.get(f"/teacher/quizzes/{q.id}/delete/").status_code, 405)

    def test_quiz_without_results_is_hard_deleted(self):
        q = self._make_quiz(self.t1)
        self.client.force_login(self.t1)
        self.client.post(f"/teacher/quizzes/{q.id}/delete/")
        self.assertFalse(Quiz.objects.filter(pk=q.pk).exists())

    def test_quiz_with_attempts_is_archived(self):
        q = self._make_quiz(self.t1)
        Attempt.objects.create(user=_make_student(), quiz=q, score=1, total=1)
        self.client.force_login(self.t1)
        self.client.post(f"/teacher/quizzes/{q.id}/delete/")
        q.refresh_from_db()
        self.assertTrue(q.is_archived)
        self.assertEqual(q.status, "disabled")


class TeacherManagementTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.boss = _make_superadmin()

    def test_superadmin_creates_teacher(self):
        self.client.force_login(self.boss)
        resp = self.client.post("/admin/teachers/add/", {
            "username": "newteach", "email": "nt@ex.com",
            "first_name": "New", "last_name": "Teach",
            "employee_id": "EMP99", "department": "CS", "phone": "123",
            "password": "Str0ngPass!42", "confirm_password": "Str0ngPass!42",
            "is_active": "on",
        })
        self.assertEqual(resp.status_code, 302)
        u = User.objects.get(username="newteach")
        self.assertTrue(u.is_staff)
        self.assertFalse(u.is_superuser)
        self.assertTrue(u.groups.filter(name=TEACHER_GROUP).exists())
        self.assertTrue(TeacherProfile.objects.filter(user=u).exists())


class StudentEditSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.t = _make_teacher()
        self.s = _make_student()

    def test_teacher_edits_safe_fields_but_cannot_escalate(self):
        self.client.force_login(self.t)
        resp = self.client.post(f"/teacher/students/{self.s.id}/edit/", {
            "first_name": "Edited", "last_name": "Name", "email": "new@ex.com",
            "roll_number": "R1", "college": "C", "branch": "CSE",
            "semester": "5", "batch": "2025", "phone": "999",
            "is_active": "on",
            # Malicious extra fields that must be ignored:
            "is_staff": "on", "is_superuser": "on",
        })
        self.assertEqual(resp.status_code, 302)
        self.s.refresh_from_db()
        self.assertEqual(self.s.first_name, "Edited")
        self.assertFalse(self.s.is_staff)      # never escalated
        self.assertFalse(self.s.is_superuser)
        self.assertEqual(self.s.student_profile.branch, "CSE")


class CsvSafetyTests(TestCase):
    def test_formula_injection_escaped(self):
        for danger in ["=cmd", "+1", "-1", "@x"]:
            self.assertTrue(csv_safe(danger).startswith("'"))
        self.assertEqual(csv_safe("normal"), "normal")


class ImportTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.t = _make_teacher()
        self.client.force_login(self.t)

    def _csv(self, text, name="f.csv"):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")

    def test_import_unknown_email_creates_pending(self):
        csv = ("email,first_name,last_name,roll_number,college,branch,semester,batch,phone\n"
               "ghost@ex.com,Ghost,User,R7,College,CSE,5,2025,111\n")
        resp = self.client.post("/teacher/students/import/",
                                {"csv_file": self._csv(csv), "confirm": "1"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(PendingStudentProfile.objects.filter(email="ghost@ex.com").exists())

    def test_import_existing_email_updates_profile(self):
        s = _make_student("existing")
        csv = ("email,first_name,last_name,roll_number,college,branch,semester,batch,phone\n"
               f"{s.email},Ex,Ist,R8,College,ECE,3,2024,222\n")
        self.client.post("/teacher/students/import/",
                         {"csv_file": self._csv(csv), "confirm": "1"})
        s.refresh_from_db()
        self.assertEqual(s.student_profile.branch, "ECE")
        self.assertFalse(PendingStudentProfile.objects.filter(email=s.email).exists())

    def test_offline_result_duplicate_rejected(self):
        cat = Category.objects.create(name="Apt")
        quiz = Quiz.objects.create(title="Q", category=cat, created_by=self.t)
        s = _make_student("resu")
        ImportedResult.objects.create(student=s, quiz=quiz, score=5, total=10,
                                      percentage=Decimal("50"), exam_date="2025-01-01",
                                      source="csv_import")
        csv = ("student_email,quiz_id,score,total,correct_count,wrong_count,marks_obtained,exam_date\n"
               f"{s.email},{quiz.id},5,10,5,5,5,2025-01-01\n")
        self.client.post("/teacher/results/import/",
                         {"csv_file": self._csv(csv), "confirm": "1"})
        # Still only the one original record — duplicate was rejected.
        self.assertEqual(ImportedResult.objects.filter(student=s, quiz=quiz).count(), 1)


class AuditLogTests(TestCase):
    def test_company_create_writes_audit_log(self):
        t = _make_teacher()
        self.client = Client()
        self.client.force_login(t)
        self.client.post("/teacher/companies/add/", {"name": "Acme", "is_active": "on"})
        self.assertTrue(AuditLog.objects.filter(action="company.create").exists())


class SetupRolesCommandTests(TestCase):
    def test_setup_roles_is_idempotent_and_least_privilege(self):
        call_command("setup_roles")
        call_command("setup_roles")  # run twice — must not error
        group = Group.objects.get(name=TEACHER_GROUP)
        codenames = set(group.permissions.values_list("codename", flat=True))
        # Never grants user deletion or permission management.
        self.assertNotIn("delete_user", codenames)
        self.assertNotIn("add_permission", codenames)
        self.assertNotIn("change_group", codenames)
        # Grants expected management perms.
        self.assertIn("add_quiz", codenames)
        self.assertIn("change_company", codenames)


class BackwardCompatTests(TestCase):
    def test_legacy_quiz_without_owner_still_accessible(self):
        cat = Category.objects.create(name="Apt")
        quiz = Quiz.objects.create(title="Legacy", category=cat)  # created_by=None
        self.assertIsNone(quiz.created_by)
        boss = _make_superadmin()
        self.assertTrue(can_manage_quiz(boss, quiz))  # super admin manages all
