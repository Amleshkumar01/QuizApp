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

    def test_staff_can_access_admin_dashboard(self):
        staff = User.objects.create_user("adminx", "adminx@example.com", "x")
        staff.is_staff = True
        staff.save()
        self.client.force_login(staff)
        resp = self.client.get("/admin/dashboard/")
        self.assertEqual(resp.status_code, 200)


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
