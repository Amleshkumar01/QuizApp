from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
import csv
import os
import random
import re
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.validators import validate_email
from django.http import HttpResponseForbidden, HttpResponse

from .decorators import admin_required, student_required, superadmin_required, teacher_required, staff_required
from .permissions import (
    is_super_admin,
    is_teacher,
    is_staff_member,
    staff_role,
    can_manage_company,
    can_manage_drive,
    can_manage_quiz,
    can_delete_quiz,
)
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .ai_service import explain_answer, generate_questions, number_question_items, personalized_suggestions, strip_question_number
from .analytics import admin_placement_stats, student_section_performance, student_weak_topics, suggested_tests_for_user
from .models import Answer, Attempt, Category, Company, Option, PlacementDrive, Question, Quiz

_LOGIN_THROTTLE_SECONDS = 900
_LOGIN_MAX_PER_USERNAME = 5
_LOGIN_MAX_PER_IP = 25
_QUIZ_SUBMIT_LOCK_SECONDS = 120

# Registration abuse protection (mass sign-up / spam accounts).
_REGISTER_THROTTLE_SECONDS = int(os.environ.get("REGISTER_THROTTLE_SECONDS", "3600"))
_REGISTER_MAX_PER_IP = int(os.environ.get("REGISTER_MAX_PER_IP", "10"))


def _valid_quiz_status(value):
    return value if value in dict(Quiz.STATUS_CHOICES) else "active"


def _staff_quiz_blocked_response(request):
    """Admins manage tests; they must not attempt quizzes as students."""
    if request.user.is_staff:
        messages.info(
            request,
            "Admin accounts cannot attempt quizzes. Use the Admin dashboard to manage tests.",
        )
        return redirect("admin_dashboard")
    return None


def _valid_difficulty(value):
    return value if value in dict(Quiz.DIFFICULTY_CHOICES) else "medium"


def _valid_section(value):
    return value if value in dict(Quiz.SECTION_CHOICES) else "aptitude"


def _category_for_section(section):
    label = dict(Quiz.SECTION_CHOICES).get(section, section.title())
    category, _ = Category.objects.get_or_create(name=label)
    return category


def _parse_decimal(value, default="0"):
    try:
        return Decimal(str(value or default))
    except (InvalidOperation, TypeError):
        return Decimal(default)


def _parse_drive_date(value):
    value = (value or "").strip()
    if not value:
        return None, None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date(), None
    except ValueError:
        return None, "Invalid drive date. Use YYYY-MM-DD."


def _save_ai_questions(quiz, items, start_number=None):
    if start_number is None:
        start_number = quiz.question_set.count() + 1
    # Store clean text (no "Q17." prefix). Questions are shuffled at quiz start,
    # so a stored number would be misleading; the UI shows the live position.
    numbered = number_question_items(items, start=start_number)
    with transaction.atomic():
        for item in numbered:
            question = Question.objects.create(
                quiz=quiz,
                text=strip_question_number(item["text"]),
                explanation=item.get("explanation", ""),
                topic=item.get("topic", ""),
                ai_generated=True,
            )
            for idx, opt_text in enumerate(item["options"][:4]):
                Option.objects.create(
                    question=question,
                    text=opt_text,
                    is_correct=idx == item["correct_index"],
                )


def _apply_quiz_form(quiz, post):
    """Populate quiz from admin placement test form."""
    quiz.title = (post.get("title") or "").strip()
    quiz.status = _valid_quiz_status((post.get("status") or quiz.status or "active").strip())
    quiz.difficulty = _valid_difficulty((post.get("difficulty") or "medium").strip())
    quiz.section = _valid_section((post.get("section") or "aptitude").strip())
    quiz.category = _category_for_section(quiz.section)

    company_id = post.get("company")
    if company_id:
        quiz.company = get_object_or_404(Company, pk=company_id)
    else:
        quiz.company = None

    try:
        quiz.duration_minutes = max(1, int(post.get("duration_minutes") or 30))
    except (TypeError, ValueError):
        quiz.duration_minutes = 30

    try:
        quiz.target_question_count = max(1, int(post.get("target_question_count") or 10))
    except (TypeError, ValueError):
        quiz.target_question_count = 10

    quiz.marks_per_question = _parse_decimal(post.get("marks_per_question"), "1")
    quiz.negative_marks = _parse_decimal(post.get("negative_marks"), "0")

    drive_date, err = _parse_drive_date(post.get("drive_date"))
    if err:
        return err
    quiz.drive_date = drive_date
    return None


def _parse_question_options(post):
    """Parse option_1..option_4 and correct_option from POST."""
    options_data = []
    for i in range(1, 5):
        text = (post.get(f"option_{i}") or "").strip()
        if text:
            options_data.append({"slot": i, "text": text})

    if len(options_data) < 2:
        return None, "At least 2 answer options are required."

    try:
        correct_slot = int(post.get("correct_option") or 0)
    except (TypeError, ValueError):
        return None, "Select one correct answer."

    filled_slots = {item["slot"] for item in options_data}
    if correct_slot not in filled_slots:
        return None, "Select the correct answer from the filled options."

    parsed = [
        {"text": item["text"], "is_correct": item["slot"] == correct_slot}
        for item in options_data
    ]
    return parsed, None


def _question_option_slots(question=None):
    """Build up to 4 option slots for add/edit forms."""
    existing = []
    if question is not None:
        existing = list(question.options.all()[:4])

    slots = []
    correct_slot = 1
    for i in range(1, 5):
        if i <= len(existing):
            opt = existing[i - 1]
            slots.append({"slot": i, "id": opt.pk, "text": opt.text, "is_correct": opt.is_correct})
            if opt.is_correct:
                correct_slot = i
        else:
            slots.append({"slot": i, "id": None, "text": "", "is_correct": False})

    if question is None:
        correct_slot = 1
        for slot in slots:
            slot["is_correct"] = slot["slot"] == correct_slot
    else:
        for slot in slots:
            slot["is_correct"] = slot["slot"] == correct_slot

    return slots, correct_slot


def _save_question_options(question, post, existing_options=None):
    """Create or update options for a question from POST fields."""
    parsed, error = _parse_question_options(post)
    if error:
        return error

    existing_by_id = {}
    if existing_options is not None:
        existing_by_id = {str(opt.pk): opt for opt in existing_options}

    kept_ids = []
    with transaction.atomic():
        for slot in range(1, 5):
            text = (post.get(f"option_{slot}") or "").strip()
            opt_id = (post.get(f"option_{slot}_id") or "").strip()
            if not text:
                continue

            is_correct = str(slot) == str(post.get("correct_option"))
            if opt_id and opt_id in existing_by_id:
                option = existing_by_id[opt_id]
                option.text = text
                option.is_correct = is_correct
                option.save(update_fields=["text", "is_correct"])
                kept_ids.append(option.pk)
            else:
                option = Option.objects.create(
                    question=question,
                    text=text,
                    is_correct=is_correct,
                )
                kept_ids.append(option.pk)

        question.options.exclude(pk__in=kept_ids).delete()

    return None


def _client_ip(request):
    return (request.META.get("REMOTE_ADDR") or "unknown").strip() or "unknown"


_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
# Raster formats only. SVG is intentionally excluded — it is XML that can carry
# embedded scripts (stored-XSS risk if opened directly from /media/).
_LOGO_ALLOWED_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _validate_logo_upload(upload):
    """Return an error string if the uploaded logo is unsafe, else None."""
    if upload is None:
        return None
    name = (getattr(upload, "name", "") or "").lower()
    if not name.endswith(_LOGO_ALLOWED_EXTS):
        return "Logo must be a PNG, JPG, GIF, or WEBP image."
    if getattr(upload, "size", 0) > _LOGO_MAX_BYTES:
        return "Logo is too large (max 2MB)."
    # Verify the bytes are actually a valid image, not a renamed payload.
    try:
        from PIL import Image

        upload.seek(0)
        Image.open(upload).verify()
    except Exception:
        return "Logo file is not a valid image."
    finally:
        try:
            upload.seek(0)
        except Exception:
            pass
    return None


def _login_throttle_keys(request, username):
    uname = (username or "").lower()[:150]
    return f"login_throttle:u:{uname}", f"login_throttle:ip:{_client_ip(request)}"


def _is_login_throttled(request, username):
    k_user, k_ip = _login_throttle_keys(request, username)
    return cache.get(k_user, 0) >= _LOGIN_MAX_PER_USERNAME or cache.get(k_ip, 0) >= _LOGIN_MAX_PER_IP


def _bump_login_throttle(request, username):
    for key in _login_throttle_keys(request, username):
        n = cache.get(key, 0) + 1
        cache.set(key, n, _LOGIN_THROTTLE_SECONDS)


def _clear_login_throttle(request, username):
    for key in _login_throttle_keys(request, username):
        cache.delete(key)


def _register_throttle_key(request):
    return f"register_throttle:ip:{_client_ip(request)}"


def _is_register_throttled(request):
    return cache.get(_register_throttle_key(request), 0) >= _REGISTER_MAX_PER_IP


def _bump_register_throttle(request):
    key = _register_throttle_key(request)
    try:
        # Atomic increment; seed the key with its TTL on first use.
        cache.add(key, 0, _REGISTER_THROTTLE_SECONDS)
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, _REGISTER_THROTTLE_SECONDS)


def _quiz_submit_lock_key(user_id, quiz_id):
    return f"quiz_submit_lock:{user_id}:{quiz_id}"


def _acquire_quiz_submit_lock(user_id, quiz_id):
    return cache.add(_quiz_submit_lock_key(user_id, quiz_id), 1, timeout=_QUIZ_SUBMIT_LOCK_SECONDS)


def _release_quiz_submit_lock(user_id, quiz_id):
    cache.delete(_quiz_submit_lock_key(user_id, quiz_id))


def _session_quiz_state_ok(quiz, question_ids):
    if not isinstance(question_ids, list) or not question_ids:
        return False
    if len(question_ids) != len(set(question_ids)):
        return False
    valid = set(quiz.question_set.values_list("id", flat=True))
    return set(question_ids) == valid


def _post_login_redirect(user):
    """Redirect authenticated user to correct dashboard."""
    if user.is_staff:
        return redirect("admin_dashboard")
    return redirect("student_dashboard")


def _question_timed_out(request):
    deadline = request.session.get("question_deadline")
    if deadline is None:
        return False
    return time.time() > float(deadline)


def _refresh_question_deadline(request):
    request.session["question_deadline"] = time.time() + settings.QUIZ_QUESTION_SECONDS
    return settings.QUIZ_QUESTION_SECONDS


def _seconds_remaining(request):
    deadline = request.session.get("question_deadline")
    if deadline is None:
        return settings.QUIZ_QUESTION_SECONDS
    return max(0, int(float(deadline) - time.time()))


def _test_seconds_remaining(request):
    deadline = request.session.get("quiz_deadline")
    if deadline is None:
        return None
    return max(0, int(float(deadline) - time.time()))


def _test_timed_out(request):
    deadline = request.session.get("quiz_deadline")
    if deadline is None:
        return False
    return time.time() > float(deadline)


def _no_store(response):
    """Prevent browsers from caching quiz pages (fixes stale page on Back)."""
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def _submit_and_redirect(request, quiz, timed_out=False):
    """Finalize the attempt (double-submit safe) and go to the result page."""
    if not _acquire_quiz_submit_lock(request.user.pk, quiz.pk):
        messages.warning(request, "This quiz is already being submitted.")
        return _no_store(redirect("my_attempts"))
    try:
        attempt = _persist_quiz_attempt(request, quiz)
    finally:
        _release_quiz_submit_lock(request.user.pk, quiz.pk)
    if timed_out:
        messages.info(request, "Time is up. Your test has been submitted.")
    return _no_store(redirect("quiz_result", attempt_id=attempt.pk))


def _quiz_attempt_context(request, quiz, current_question, options, question_index, question_ids):
    answers = request.session.get("answers", {})
    selected_option_id = answers.get(str(current_question.id))
    total = len(question_ids)

    palette = []
    answered_count = 0
    for i, qid in enumerate(question_ids):
        is_answered = str(qid) in answers
        if is_answered:
            answered_count += 1
        palette.append({
            "num": i + 1,
            "index": i,
            "answered": is_answered,
            "current": i == question_index,
        })

    test_remaining = _test_seconds_remaining(request)
    return {
        "quiz": quiz,
        "question": current_question,
        "options": options,
        "selected_option_id": selected_option_id,
        "question_number": question_index + 1,
        "question_index": question_index,
        "total_questions": total,
        "answered_count": answered_count,
        "unanswered_count": total - answered_count,
        "is_first_question": question_index == 0,
        "is_last_question": question_index + 1 >= total,
        "palette": palette,
        "uses_test_timer": test_remaining is not None,
        "seconds_remaining": test_remaining if test_remaining is not None else 0,
    }


def _clear_quiz_session(request):
    for key in (
        "quiz_id",
        "question_index",
        "question_ids",
        "answers",
        "question_deadline",
        "quiz_started_at",
        "quiz_deadline",
    ):
        request.session.pop(key, None)


def _persist_quiz_attempt(request, quiz):
    """Create Attempt + Answers from session; clears quiz session keys. Caller must ensure quiz matches session."""
    question_ids = request.session.get("question_ids") or list(
        quiz.question_set.values_list("id", flat=True)
    )
    total_questions = len(question_ids)
    answers = request.session.get("answers", {})
    started_at_ts = request.session.get("quiz_started_at")
    time_spent = int(time.time() - float(started_at_ts)) if started_at_ts else 0
    started_at = timezone.now() - timedelta(seconds=time_spent) if time_spent else None

    correct = 0
    wrong = 0
    marks = Decimal("0")

    with transaction.atomic():
        attempt = Attempt.objects.create(
            user=request.user,
            quiz=quiz,
            score=0,
            total=total_questions,
            correct_count=0,
            wrong_count=0,
            marks_obtained=Decimal("0"),
            time_spent_seconds=time_spent,
            started_at=started_at,
        )
        for qid, oid in answers.items():
            try:
                question = Question.objects.get(pk=qid, quiz=quiz)
                selected_option = Option.objects.get(pk=oid, question=question)
            except (Question.DoesNotExist, Option.DoesNotExist):
                continue
            is_correct = selected_option.is_correct
            Answer.objects.update_or_create(
                attempt=attempt,
                question=question,
                defaults={"selected_option": selected_option, "is_correct": is_correct},
            )
            if is_correct:
                correct += 1
                marks += quiz.marks_per_question
            else:
                wrong += 1
                marks -= quiz.negative_marks

        attempt.score = correct
        attempt.correct_count = correct
        attempt.wrong_count = wrong
        attempt.marks_obtained = marks
        attempt.save(update_fields=["score", "correct_count", "wrong_count", "marks_obtained"])

    _clear_quiz_session(request)
    return attempt


def _init_quiz_attempt(request, quiz):
    _clear_quiz_session(request)
    question_ids = list(quiz.question_set.values_list("id", flat=True))
    if quiz.target_question_count and len(question_ids) > quiz.target_question_count:
        question_ids = random.sample(question_ids, quiz.target_question_count)
    else:
        random.shuffle(question_ids)
    now = time.time()
    request.session["quiz_id"] = quiz.id
    request.session["question_ids"] = question_ids
    request.session["question_index"] = 0
    request.session["answers"] = {}
    request.session["quiz_started_at"] = now
    if quiz.duration_minutes:
        request.session["quiz_deadline"] = now + quiz.duration_minutes * 60
    else:
        request.session["quiz_deadline"] = None


def django_admin_blocked(request):
    """Block public access to the default Django /admin/ URL."""
    return HttpResponseForbidden(
        "Access denied. The default admin URL is disabled. "
        "Use the authorized admin login page if you are an administrator."
    )


def _staff_post_login_redirect(user):
    """Route a staff user to the correct portal based on role."""
    if is_super_admin(user):
        return redirect("admin_dashboard")
    if is_teacher(user):
        return redirect("teacher_dashboard")
    # Staff flag set but not a recognised role: treat as admin for safety.
    return redirect("admin_dashboard")


@require_http_methods(["GET", "POST"])
def admin_login_view(request):
    if request.user.is_authenticated:
        if request.user.is_staff:
            return _staff_post_login_redirect(request.user)
        messages.warning(request, "Student accounts must use the student login page.")
        return redirect("student_dashboard")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        if _is_login_throttled(request, username):
            messages.error(
                request,
                "Too many login attempts. Please wait a few minutes and try again.",
            )
            return redirect("admin_login")

        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_staff:
            _clear_login_throttle(request, username)
            request.session.cycle_key()
            login(request, user)
            messages.success(request, f"Welcome, {username}!")
            return _staff_post_login_redirect(user)

        _bump_login_throttle(request, username)
        if user is not None and not user.is_staff:
            messages.error(request, "This login is for administrators only. Use student login.")
        else:
            messages.error(request, "Invalid admin credentials.")
        return redirect("admin_login")

    return render(request, "admin_login.html")


@require_http_methods(["GET", "POST"])
def student_login_view(request):
    if request.user.is_authenticated:
        return _post_login_redirect(request.user)

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        if _is_login_throttled(request, username):
            messages.error(
                request,
                "Too many login attempts. Please wait a few minutes and try again.",
            )
            return render(request, "login.html", {"form_username": username})

        # Allow login with email or username
        user = authenticate(request, username=username, password=password)
        if user is None and "@" in username:
            # Try email lookup. Use filter().first() so duplicate emails (should
            # not exist, but be defensive) never raise MultipleObjectsReturned.
            email_user = (
                User.objects.filter(email__iexact=username).order_by("pk").first()
            )
            if email_user is not None:
                user = authenticate(request, username=email_user.username, password=password)

        if user is not None:
            if user.is_staff:
                messages.error(request, "Admin accounts must use the admin login page.")
                return redirect("admin_login")

            _clear_login_throttle(request, username)
            request.session.cycle_key()
            login(request, user)
            display_name = user.first_name or user.username
            messages.success(request, f"Welcome back, {display_name}!")
            return redirect("student_dashboard")

        _bump_login_throttle(request, username)
        messages.error(request, "Invalid username/email or password.")
        return render(request, "login.html", {"form_username": username})

    return render(request, "login.html")


def login_view(request):
    """Legacy URL — redirect to student login."""
    return redirect("student_login")


@login_required
@require_POST
def logout_view(request):
    was_staff = request.user.is_staff
    logout(request)
    messages.info(request, "You have been logged out.")
    if was_staff:
        return redirect("admin_login")
    return redirect("student_login")


@student_required
def student_dashboard(request):
    companies = (
        Company.objects.filter(is_active=True)
        .annotate(test_count=Count(
            "tests",
            filter=Q(tests__status="active", tests__is_archived=False, tests__question__isnull=False),
            distinct=True,
        ))
        .order_by("name")
    )
    recent_attempts = (
        Attempt.objects.filter(user=request.user)
        .select_related("quiz", "quiz__company")
        .order_by("-completed_at")[:5]
    )
    user_attempts = Attempt.objects.filter(user=request.user)
    total_attempts = user_attempts.count()
    best_score = (
        user_attempts
        .order_by("-marks_obtained", "-score")
        .values_list("marks_obtained", flat=True)
        .first()
    )
    # Average accuracy percentage
    avg_accuracy = 0
    if total_attempts > 0:
        from django.db.models import Sum
        totals = user_attempts.aggregate(
            total_correct=Sum("correct_count"),
            total_questions=Sum("total"),
        )
        if totals["total_questions"] and totals["total_questions"] > 0:
            avg_accuracy = round(
                (totals["total_correct"] or 0) / totals["total_questions"] * 100
            )

    upcoming_drives = (
        PlacementDrive.objects.filter(drive_date__gte=date.today(), company__is_active=True)
        .exclude(status="cancelled")
        .select_related("company")
        .order_by("drive_date")[:4]
    )
    return render(
        request,
        "student_dashboard.html",
        {
            "companies": companies,
            "recent_attempts": recent_attempts,
            "total_attempts": total_attempts,
            "best_score": best_score,
            "avg_accuracy": avg_accuracy,
            "upcoming_drives": upcoming_drives,
        },
    )


def home(request):
    companies = (
        Company.objects.filter(is_active=True)
        .annotate(test_count=Count(
            "tests",
            filter=Q(tests__status="active", tests__is_archived=False, tests__question__isnull=False),
            distinct=True,
        ))
        .order_by("name")
    )
    upcoming_drives = (
        PlacementDrive.objects.filter(drive_date__gte=date.today(), company__is_active=True)
        .exclude(status="cancelled")
        .select_related("company")
        .order_by("drive_date")[:6]
    )
    return render(
        request,
        "home.html",
        {"companies": companies, "upcoming_drives": upcoming_drives},
    )


def company_tests(request, company_id):
    company = get_object_or_404(Company, pk=company_id, is_active=True)
    difficulty = request.GET.get("level", "").strip()
    section = request.GET.get("section", "").strip()

    practice_section = section if section in dict(Quiz.SECTION_CHOICES) else "aptitude"
    practice_level = difficulty if difficulty in dict(Quiz.DIFFICULTY_CHOICES) else "medium"

    # NOTE: We NEVER auto-create quizzes here. A quiz only exists if a teacher/
    # admin explicitly created it. Opening a company must not touch the database.
    tests = (
        Quiz.objects.filter(company=company, status="active", is_archived=False)
        .annotate(total_questions=Count("question"))
        .filter(total_questions__gt=0)
        .order_by("section", "difficulty", "title")
    )
    if difficulty in dict(Quiz.DIFFICULTY_CHOICES):
        tests = tests.filter(difficulty=difficulty)
    if section in dict(Quiz.SECTION_CHOICES):
        tests = tests.filter(section=section)

    return render(
        request,
        "company_tests.html",
        {
            "company": company,
            "tests": tests,
            "difficulty_choices": Quiz.DIFFICULTY_CHOICES,
            "section_choices": Quiz.SECTION_CHOICES,
            "active_level": difficulty,
            "active_section": section,
            "default_section": practice_section,
            "default_level": practice_level,
        },
    )


@require_http_methods(["GET", "POST"])
def register(request):
    """Redirect legacy /register/ to the new signup page."""
    return redirect("student_register")


@require_http_methods(["GET", "POST"])
def student_register(request):
    """Student self-registration with Django auth."""
    if request.user.is_authenticated:
        return _post_login_redirect(request.user)

    form_data = {}
    if request.method == "POST":
        # Rate-limit sign-ups per IP to curb bot / mass-account abuse.
        # Tune with REGISTER_MAX_PER_IP (raise it for shared campus NAT networks).
        if _is_register_throttled(request):
            messages.error(
                request,
                "Too many sign-up attempts from this network. Please try again later.",
            )
            return render(request, "register.html")

        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        first_name = (request.POST.get("first_name") or "").strip()[:150]
        last_name = (request.POST.get("last_name") or "").strip()[:150]
        password = request.POST.get("password") or ""
        confirm_password = request.POST.get("confirm_password") or ""

        form_data = {
            "form_username": username,
            "form_email": email,
            "form_first_name": first_name,
            "form_last_name": last_name,
        }

        _bump_register_throttle(request)

        # Validation
        errors = []
        if not username:
            errors.append("Username is required.")
        elif len(username) < 3 or len(username) > 150:
            errors.append("Username must be between 3 and 150 characters.")
        elif not re.match(r'^[\w.@+-]+$', username):
            errors.append("Username can only contain letters, digits, and @/./+/-/_ characters.")

        if not email:
            errors.append("Email is required.")
        elif len(email) > 254:
            errors.append("Email address is too long.")
        else:
            try:
                validate_email(email)
            except ValidationError:
                errors.append("Enter a valid email address.")

        if not password:
            errors.append("Password is required.")
        elif password != confirm_password:
            errors.append("Passwords do not match.")

        if not errors:
            try:
                validate_password(password, user=User(username=username, email=email))
            except ValidationError as e:
                errors.extend(e.messages)

        if not errors and User.objects.filter(username__iexact=username).exists():
            errors.append("This username is already taken.")

        if not errors and User.objects.filter(email__iexact=email).exists():
            errors.append("An account with this email already exists.")

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "register.html", form_data)

        # Create user (guard against a race between the checks above and insert).
        try:
            with transaction.atomic():
                if User.objects.filter(Q(username__iexact=username) | Q(email__iexact=email)).exists():
                    raise IntegrityError("duplicate user")
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    is_staff=False,
                )
        except IntegrityError:
            messages.error(request, "That username or email is already registered.")
            return render(request, "register.html", form_data)

        login(request, user)
        messages.success(request, f"Welcome to PlacementIQ, {first_name or username}! Your account is ready.")
        return redirect("student_dashboard")

    return render(request, "register.html", form_data)


def category_quizzes(request, category_id):
    category = get_object_or_404(Category, id=category_id)
    quizzes = Quiz.objects.filter(category=category).annotate(total_questions=Count("question"))
    if not (request.user.is_authenticated and request.user.is_staff):
        quizzes = quizzes.filter(status="active")
    return render(
        request,
        "quizzes_by_category.html",
        {"quizzes": quizzes, "category": category},
    )


@student_required
def start_quiz(request, quiz_id):
    blocked = _staff_quiz_blocked_response(request)
    if blocked:
        return blocked

    quiz = get_object_or_404(Quiz, id=quiz_id)

    if quiz.status != "active":
        messages.warning(request, "This quiz is not currently active.")
        return redirect("home")

    if not quiz.question_set.exists():
        messages.warning(request, "This quiz has no questions.")
        return redirect("home")

    # A quiz is ONLY initialized here (via the Start button), never elsewhere.
    _init_quiz_attempt(request, quiz)
    return _no_store(redirect("attempt_quiz", quiz_id=quiz.id))


def _save_current_answer(request, question_ids, current_index):
    """Persist the submitted option for the current question into the session
    answers dict. Skipping (no option) is allowed and leaves any prior answer."""
    option_id = request.POST.get("option")
    if not option_id:
        return
    try:
        oid = int(option_id)
    except (TypeError, ValueError):
        return
    if not (0 <= current_index < len(question_ids)):
        return
    current_qid = question_ids[current_index]
    if Option.objects.filter(pk=oid, question_id=current_qid).exists():
        answers = request.session.get("answers", {})
        answers[str(current_qid)] = oid
        request.session["answers"] = answers
        request.session.modified = True


@student_required
def attempt_quiz(request, quiz_id):
    blocked = _staff_quiz_blocked_response(request)
    if blocked:
        _clear_quiz_session(request)
        return blocked

    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.status != "active":
        messages.warning(request, "This quiz is not currently active.")
        return _no_store(redirect("student_dashboard"))

    # NO auto-initialize. If there is no valid active session for THIS quiz,
    # send the student to their dashboard (fixes browser-Back re-opening a quiz).
    question_ids = request.session.get("question_ids")
    if request.session.get("quiz_id") != quiz.id or not _session_quiz_state_ok(quiz, question_ids):
        messages.info(request, "Please start the test using the Start Quiz button.")
        return _no_store(redirect("student_dashboard"))

    total = len(question_ids)

    # Test timer expired → auto final submit.
    if _test_timed_out(request):
        return _submit_and_redirect(request, quiz, timed_out=True)

    if request.method == "POST":
        try:
            current_index = int(request.POST.get("current_index", request.session.get("question_index", 0)))
        except (TypeError, ValueError):
            current_index = 0
        current_index = max(0, min(current_index, total - 1))

        # Always save the current selection (skip allowed if none chosen).
        _save_current_answer(request, question_ids, current_index)

        goto = request.POST.get("goto")
        action = (request.POST.get("action") or "").strip()
        if not action and goto is not None:
            action = "jump"

        if action == "timeout" or _test_timed_out(request):
            return _submit_and_redirect(request, quiz, timed_out=True)

        if action == "submit":
            request.session["question_index"] = current_index
            return _no_store(redirect("quiz_confirm"))

        # Navigation
        if action == "prev":
            target = current_index - 1
        elif action == "jump":
            try:
                target = int(goto)
            except (TypeError, ValueError):
                target = current_index
        else:  # "next" (default)
            target = current_index + 1

        request.session["question_index"] = max(0, min(target, total - 1))
        return _no_store(redirect("attempt_quiz", quiz_id=quiz.id))

    # GET — allow ?q=<index> deep link/jump
    q_param = request.GET.get("q")
    if q_param is not None:
        try:
            request.session["question_index"] = max(0, min(int(q_param), total - 1))
        except (TypeError, ValueError):
            pass

    question_index = request.session.get("question_index", 0)
    try:
        question_index = int(question_index)
    except (TypeError, ValueError):
        question_index = 0
    question_index = max(0, min(question_index, total - 1))
    request.session["question_index"] = question_index

    current_question = get_object_or_404(Question, id=question_ids[question_index], quiz=quiz)
    options = current_question.options.all()

    return _no_store(render(
        request,
        "quiz_attempt.html",
        _quiz_attempt_context(request, quiz, current_question, options, question_index, question_ids),
    ))


@student_required
def quiz_confirm(request):
    """Confirmation screen shown before final submission."""
    blocked = _staff_quiz_blocked_response(request)
    if blocked:
        _clear_quiz_session(request)
        return blocked

    quiz_id = request.session.get("quiz_id")
    question_ids = request.session.get("question_ids")
    if not quiz_id or not isinstance(question_ids, list) or not question_ids:
        messages.info(request, "No active quiz session.")
        return _no_store(redirect("student_dashboard"))

    quiz = get_object_or_404(Quiz, pk=quiz_id)
    if not _session_quiz_state_ok(quiz, question_ids):
        messages.info(request, "Quiz session expired or was reset.")
        return _no_store(redirect("student_dashboard"))

    if _test_timed_out(request):
        return _submit_and_redirect(request, quiz, timed_out=True)

    answers = request.session.get("answers", {})
    total = len(question_ids)
    answered = sum(1 for qid in question_ids if str(qid) in answers)

    return _no_store(render(request, "quiz_confirm_submit.html", {
        "quiz": quiz,
        "total_questions": total,
        "answered_count": answered,
        "unanswered_count": total - answered,
        "current_index": request.session.get("question_index", 0),
    }))


@student_required
@require_POST
def finalize_quiz(request):
    """POST-only final submission. A student may submit at any time — even with
    unanswered questions (they simply score 0 / count as skipped)."""
    blocked = _staff_quiz_blocked_response(request)
    if blocked:
        _clear_quiz_session(request)
        return blocked

    quiz_id = request.session.get("quiz_id")
    question_ids = request.session.get("question_ids")

    if not quiz_id or not isinstance(question_ids, list) or not question_ids:
        messages.info(request, "No active quiz session.")
        return _no_store(redirect("student_dashboard"))

    quiz = get_object_or_404(Quiz, pk=quiz_id)

    if not _session_quiz_state_ok(quiz, question_ids):
        messages.info(request, "Quiz session expired or was reset.")
        return _no_store(redirect("student_dashboard"))

    return _submit_and_redirect(request, quiz)


@student_required
def quiz_result(request, attempt_id):
    attempt = get_object_or_404(
        Attempt.objects.select_related("quiz", "quiz__company"),
        pk=attempt_id,
        user=request.user,
    )
    percentage = round((attempt.score / attempt.total) * 100) if attempt.total else 0
    # Skipped needs no DB field: total minus answered (correct + wrong).
    skipped = max(0, attempt.total - attempt.correct_count - attempt.wrong_count)
    return _no_store(render(
        request,
        "quiz_result.html",
        {
            "score": attempt.score,
            "total_questions": attempt.total,
            "quiz": attempt.quiz,
            "percentage": percentage,
            "attempt": attempt,
            "wrong_count": attempt.wrong_count,
            "correct_count": attempt.correct_count,
            "skipped_count": skipped,
        },
    ))


@student_required
def my_attempts(request):
    attempts = (
        Attempt.objects.filter(user=request.user)
        .select_related("quiz", "quiz__company")
        .order_by("-completed_at")
    )
    return render(request, "my_attempts.html", {"attempts": attempts})


@student_required
def student_profile_view(request):
    """Student can view and update their own profile (safe fields only)."""
    from .models import StudentProfile

    profile, _ = StudentProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        # Only allow safe fields — never is_staff, is_superuser, groups, password
        request.user.first_name = (request.POST.get("first_name") or "").strip()[:150]
        request.user.last_name = (request.POST.get("last_name") or "").strip()[:150]
        request.user.save(update_fields=["first_name", "last_name"])

        profile.roll_number = (request.POST.get("roll_number") or "").strip()[:50] or None
        profile.college = (request.POST.get("college") or "").strip()[:200]
        profile.branch = (request.POST.get("branch") or "").strip()[:120]
        profile.phone = (request.POST.get("phone") or "").strip()[:20]
        profile.batch = (request.POST.get("batch") or "").strip()[:50]
        sem = (request.POST.get("semester") or "").strip()
        if sem:
            try:
                sem_val = int(sem)
                if 1 <= sem_val <= 12:
                    profile.semester = sem_val
            except ValueError:
                pass
        else:
            profile.semester = None
        try:
            profile.save()
            messages.success(request, "Profile updated successfully!")
        except IntegrityError:
            messages.error(request, "That roll number is already taken by another student.")
        return redirect("student_profile")

    return render(request, "student_profile.html", {
        "profile": profile,
    })


@student_required
def student_analytics(request):
    weak_topics = student_weak_topics(request.user)
    section_stats = list(student_section_performance(request.user))
    suggested = suggested_tests_for_user(request.user)
    return render(
        request,
        "student_analytics.html",
        {
            "weak_topics": weak_topics,
            "section_stats": section_stats,
            "suggested_tests": suggested,
        },
    )


@student_required
def ai_practice_plan(request):
    weak = [row["question__topic"] for row in student_weak_topics(request.user, limit=5)]
    company_name = (request.GET.get("company") or "").strip() or None
    tips = personalized_suggestions(weak, company_name)
    suggested = suggested_tests_for_user(request.user)
    return render(
        request,
        "ai_practice_plan.html",
        {
            "weak_topics": weak,
            "tips": tips,
            "suggested_tests": suggested,
            "company_name": company_name,
        },
    )


@admin_required
def admin_dashboard(request):
    placement = admin_placement_stats()
    context = {
        "total_users": User.objects.filter(is_staff=False).count(),
        "total_quizzes": Quiz.objects.count(),
        "total_attempts": Attempt.objects.count(),
        "total_companies": Company.objects.filter(is_active=True).count(),
        "top_quizzes": Quiz.objects.annotate(attempts=Count("attempt")).order_by("-attempts")[:5],
        "by_company": placement["by_company"],
        "recent_attempts": placement["recent_attempts"],
    }
    return render(request, "admin_dashboard.html", context)


@admin_required
def admin_analytics(request):
    placement = admin_placement_stats()
    return render(
        request,
        "admin_analytics.html",
        {
            "by_company": placement["by_company"],
            "by_section": placement["by_section"],
            "recent_attempts": placement["recent_attempts"],
        },
    )


@admin_required
def admin_manage_users(request):
    users = User.objects.filter(is_staff=False).order_by("username")
    return render(request, "admin_users.html", {"users": users})


@admin_required
def admin_manage_quizzes(request):
    quizzes = (
        Quiz.objects.select_related("company", "category")
        .annotate(question_count=Count("question"))
        .order_by("-created_at")
    )
    level = (request.GET.get("level") or "").strip()
    if level in dict(Quiz.DIFFICULTY_CHOICES):
        quizzes = quizzes.filter(difficulty=level)
    return render(
        request,
        "admin_quizzes.html",
        {"quizzes": quizzes, "selected_level": level},
    )


@admin_required
def admin_add_quiz(request):
    companies = Company.objects.filter(is_active=True).order_by("name")
    if request.method == "POST":
        quiz = Quiz(title="")
        err = _apply_quiz_form(quiz, request.POST)
        if err:
            messages.error(request, err)
            return redirect("admin_add_quiz")
        if not quiz.title:
            messages.error(request, "Title is required.")
            return redirect("admin_add_quiz")
        if not quiz.company_id:
            messages.error(request, "Select a company for placement tests.")
            return redirect("admin_add_quiz")

        quiz.save()
        messages.success(request, "Placement test created. Add questions manually or generate with AI.")
        return redirect("admin_quiz_questions", quiz_id=quiz.id)

    return render(
        request,
        "admin_add_quiz.html",
        {
            "companies": companies,
            "difficulty_choices": Quiz.DIFFICULTY_CHOICES,
            "section_choices": Quiz.SECTION_CHOICES,
        },
    )


@admin_required
def admin_edit_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    companies = Company.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        err = _apply_quiz_form(quiz, request.POST)
        if err:
            messages.error(request, err)
            return redirect("admin_edit_quiz", quiz_id=quiz.id)
        if not quiz.title:
            messages.error(request, "Title is required.")
            return redirect("admin_edit_quiz", quiz_id=quiz.id)
        quiz.save()
        messages.success(request, "Test updated successfully.")
        return redirect("admin_manage_quizzes")

    return render(
        request,
        "admin_add_quiz.html",
        {
            "quiz": quiz,
            "companies": companies,
            "difficulty_choices": Quiz.DIFFICULTY_CHOICES,
            "section_choices": Quiz.SECTION_CHOICES,
        },
    )


@admin_required
@require_POST
def admin_delete_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    quiz.delete()
    messages.success(request, "Quiz deleted.")
    return redirect("admin_manage_quizzes")


@admin_required
def admin_quiz_questions(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    questions = quiz.question_set.prefetch_related("options").all()
    return render(
        request,
        "admin_quiz_questions.html",
        {"quiz": quiz, "questions": questions},
    )


@admin_required
def admin_add_question(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)

    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()
        if not text:
            messages.error(request, "Question text is required.")
            return redirect("admin_add_question", quiz_id=quiz.id)

        _, error = _parse_question_options(request.POST)
        if error:
            messages.error(request, error)
            return redirect("admin_add_question", quiz_id=quiz.id)

        explanation = (request.POST.get("explanation") or "").strip()
        topic = (request.POST.get("topic") or "").strip()

        with transaction.atomic():
            question = Question.objects.create(
                quiz=quiz, text=text, explanation=explanation, topic=topic
            )
            _save_question_options(question, request.POST)

        messages.success(request, "Question and answers added successfully.")
        return redirect("admin_quiz_questions", quiz_id=quiz.id)

    option_slots, correct_slot = _question_option_slots()
    return render(
        request,
        "admin_add_question.html",
        {"quiz": quiz, "option_slots": option_slots, "correct_slot": correct_slot},
    )


@admin_required
def admin_edit_question(request, question_id):
    question = get_object_or_404(Question.objects.prefetch_related("options"), pk=question_id)
    quiz = question.quiz
    options = list(question.options.all()[:4])

    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()
        if not text:
            messages.error(request, "Question text is required.")
            return redirect("admin_edit_question", question_id=question.id)

        _, error = _parse_question_options(request.POST)
        if error:
            messages.error(request, error)
            return redirect("admin_edit_question", question_id=question.id)

        question.text = text
        question.explanation = (request.POST.get("explanation") or "").strip()
        question.topic = (request.POST.get("topic") or "").strip()
        question.save(update_fields=["text", "explanation", "topic"])
        error = _save_question_options(question, request.POST, existing_options=options)
        if error:
            messages.error(request, error)
            return redirect("admin_edit_question", question_id=question.id)

        messages.success(request, "Question updated successfully.")
        return redirect("admin_quiz_questions", quiz_id=quiz.id)

    option_slots, correct_slot = _question_option_slots(question)
    return render(
        request,
        "admin_add_question.html",
        {
            "quiz": quiz,
            "question": question,
            "option_slots": option_slots,
            "correct_slot": correct_slot,
        },
    )


@admin_required
@require_POST
def admin_delete_question(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    quiz_id = question.quiz_id
    question.delete()
    messages.success(request, "Question deleted.")
    return redirect("admin_quiz_questions", quiz_id=quiz_id)


@admin_required
def admin_ai_generate_questions(request, quiz_id):
    quiz = get_object_or_404(Quiz.objects.select_related("company"), pk=quiz_id)

    if request.method == "POST":
        try:
            count = int(request.POST.get("count") or quiz.target_question_count or 10)
        except (TypeError, ValueError):
            count = 10
        count = max(1, min(count, 20))

        company_name = quiz.company.name if quiz.company else "Campus Placement"
        items, source = generate_questions(company_name, quiz.section, quiz.difficulty, count)
        allow_fallback = getattr(settings, "AI_ALLOW_FALLBACK", False)

        # Reject only when there is nothing to save, or AI failed AND fallback
        # is disabled (strict mode).
        if not items or (source != "ai" and not allow_fallback):
            messages.error(
                request,
                "AI question generation failed. No questions were added. "
                "Set OPENAI_API_KEY (or AI_ALLOW_FALLBACK=true) in .env, try again, "
                "or add questions manually.",
            )
            return redirect("admin_ai_generate", quiz_id=quiz.id)
        start_num = quiz.question_set.count() + 1
        _save_ai_questions(quiz, items, start_number=start_num)
        if source == "ai":
            messages.success(request, f"Generated {len(items)} AI questions.")
        else:
            messages.success(request, f"Added {len(items)} questions from the built-in question bank.")
        return redirect("admin_quiz_questions", quiz_id=quiz.id)

    return render(request, "admin_ai_generate.html", {"quiz": quiz})


@admin_required
def upload_quizzes_csv(request):
    if request.method == "POST":
        if "csv_file" not in request.FILES:
            messages.error(request, "CSV file missing.")
            return redirect("admin_manage_quizzes")

        csv_file = request.FILES["csv_file"]
        if not getattr(csv_file, "name", "").lower().endswith(".csv"):
            messages.error(request, "Please upload a .csv file.")
            return redirect("admin_manage_quizzes")
        if getattr(csv_file, "size", 0) > 2 * 1024 * 1024:
            messages.error(request, "CSV file is too large (max 2MB).")
            return redirect("admin_manage_quizzes")

        try:
            file_data = TextIOWrapper(csv_file.file, encoding="utf-8")
            reader = csv.DictReader(file_data)
            rows = list(enumerate(reader))
        except (UnicodeDecodeError, csv.Error):
            messages.error(request, "Could not read the CSV. Ensure it is a valid UTF-8 .csv file.")
            return redirect("admin_manage_quizzes")

        max_rows = 1000
        created = 0
        with transaction.atomic():
            for idx, row in rows:
                if idx >= max_rows:
                    break

                title = (row.get("title") or "").strip()[:255]
                category_name = (row.get("category") or "").strip()[:100]
                status = _valid_quiz_status((row.get("status") or "active").strip())
                if not title or not category_name:
                    continue

                category, _ = Category.objects.get_or_create(name=category_name)
                Quiz.objects.create(title=title, category=category, status=status)
                created += 1

        messages.success(request, f"Quizzes uploaded successfully ({created} added).")
        return redirect("admin_manage_quizzes")

    return render(request, "admin_upload_quizzes.html")


@admin_required
def admin_add_user(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""

        if not username or not email:
            messages.error(request, "Username and email are required.")
            return redirect("admin_add_user")

        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, "Enter a valid email address.")
            return redirect("admin_add_user")

        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("admin_add_user")

        # Enforce email uniqueness so email-based login stays unambiguous.
        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "A user with this email already exists.")
            return redirect("admin_add_user")

        try:
            validate_password(password, user=User(username=username, email=email))
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return redirect("admin_add_user")

        try:
            with transaction.atomic():
                if User.objects.filter(Q(username__iexact=username) | Q(email__iexact=email)).exists():
                    raise IntegrityError("duplicate user")
                User.objects.create_user(username=username, email=email, password=password, is_staff=False)
        except IntegrityError:
            messages.error(request, "That username or email is already registered.")
            return redirect("admin_add_user")

        messages.success(request, "User created successfully.")
        return redirect("admin_manage_users")

    return render(request, "admin_add_user.html")


@admin_required
@require_POST
def delete_user(request, user_id):
    if user_id == request.user.id:
        messages.error(request, "You cannot delete your own account.")
        return redirect("admin_manage_users")

    user = get_object_or_404(User, id=user_id)
    if user.is_superuser and not request.user.is_superuser:
        messages.error(request, "You cannot delete that account.")
        return redirect("admin_manage_users")

    user.delete()
    messages.success(request, "User deleted.")
    return redirect("admin_manage_users")


@admin_required
def upload_users_csv(request):
    if request.method == "POST":
        if "csv_file" not in request.FILES:
            messages.error(request, "CSV file missing.")
            return redirect("admin_manage_users")

        csv_file = request.FILES["csv_file"]
        if not getattr(csv_file, "name", "").lower().endswith(".csv"):
            messages.error(request, "Please upload a .csv file.")
            return redirect("admin_manage_users")
        if getattr(csv_file, "size", 0) > 2 * 1024 * 1024:
            messages.error(request, "CSV file is too large (max 2MB).")
            return redirect("admin_manage_users")

        try:
            file_data = TextIOWrapper(csv_file.file, encoding="utf-8")
            reader = csv.DictReader(file_data)
            rows = list(enumerate(reader))
        except (UnicodeDecodeError, csv.Error):
            messages.error(request, "Could not read the CSV. Ensure it is a valid UTF-8 .csv file.")
            return redirect("admin_manage_users")

        max_rows = 1000
        created = 0
        skipped = 0
        with transaction.atomic():
            for idx, row in rows:
                if idx >= max_rows:
                    break

                username = (row.get("username") or "").strip()
                email = (row.get("email") or "").strip()
                password = row.get("password") or ""
                if not username or not email or not password:
                    skipped += 1
                    continue

                # Validate email format and enforce username + email uniqueness.
                try:
                    validate_email(email)
                except ValidationError:
                    skipped += 1
                    continue

                if User.objects.filter(Q(username__iexact=username) | Q(email__iexact=email)).exists():
                    skipped += 1
                    continue

                try:
                    validate_password(password, user=User(username=username, email=email))
                except ValidationError:
                    skipped += 1
                    continue

                User.objects.create_user(username=username, email=email, password=password, is_staff=False)
                created += 1

        msg = f"Users uploaded successfully ({created} added)."
        if skipped:
            msg += f" {skipped} row(s) skipped (invalid, duplicate, or weak password)."
        messages.success(request, msg)
        return redirect("admin_manage_users")

    return render(request, "admin_upload_users.html")


@admin_required
def edit_user(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if user.is_superuser and not request.user.is_superuser:
        messages.error(request, "You cannot edit that account.")
        return redirect("admin_manage_users")

    if request.method == "POST":
        new_username = (request.POST.get("username") or "").strip()
        new_email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""

        if not new_username or not new_email:
            messages.error(request, "Username and email are required.")
            return redirect("edit_user", user_id=user.id)

        if User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
            messages.error(request, "That username is already taken.")
            return redirect("edit_user", user_id=user.id)

        user.username = new_username
        user.email = new_email

        if password:
            try:
                validate_password(password, user=user)
            except ValidationError as e:
                for err in e.messages:
                    messages.error(request, err)
                return redirect("edit_user", user_id=user.id)
            user.set_password(password)

        user.save()
        messages.success(request, "User updated successfully.")
        return redirect("admin_manage_users")

    return render(request, "admin_edit_user.html", {"user": user})


@admin_required
def admin_manage_companies(request):
    companies = (
        Company.objects.annotate(test_count=Count("tests"))
        .order_by("name")
    )
    return render(request, "admin_companies.html", {"companies": companies})


@admin_required
def admin_add_company(request):
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        is_active = request.POST.get("is_active") == "on"

        if not name:
            messages.error(request, "Company name is required.")
            return redirect("admin_add_company")

        if Company.objects.filter(name__iexact=name).exists():
            messages.error(request, "A company with this name already exists.")
            return redirect("admin_add_company")

        logo = request.FILES.get("logo")
        logo_error = _validate_logo_upload(logo)
        if logo_error:
            messages.error(request, logo_error)
            return redirect("admin_add_company")

        company = Company.objects.create(
            name=name,
            description=description,
            is_active=is_active,
        )
        if logo:
            company.logo = logo
            company.save(update_fields=["logo"])

        messages.success(request, "Company created successfully.")
        return redirect("admin_manage_companies")

    return render(request, "admin_add_company.html")


@admin_required
def admin_edit_company(request, company_id):
    company = get_object_or_404(Company, pk=company_id)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        is_active = request.POST.get("is_active") == "on"

        if not name:
            messages.error(request, "Company name is required.")
            return redirect("admin_edit_company", company_id=company.id)

        if Company.objects.filter(name__iexact=name).exclude(pk=company.pk).exists():
            messages.error(request, "A company with this name already exists.")
            return redirect("admin_edit_company", company_id=company.id)

        logo = request.FILES.get("logo")
        logo_error = _validate_logo_upload(logo)
        if logo_error:
            messages.error(request, logo_error)
            return redirect("admin_edit_company", company_id=company.id)

        company.name = name
        company.description = description
        company.is_active = is_active
        if logo:
            company.logo = logo
        company.save()

        messages.success(request, "Company updated successfully.")
        return redirect("admin_manage_companies")

    return render(request, "admin_add_company.html", {"company": company})


@admin_required
@require_POST
def admin_delete_company(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    company.delete()
    messages.success(request, "Company deleted.")
    return redirect("admin_manage_companies")


@admin_required
def admin_manage_test_levels(request):
    levels = []
    for value, label in Quiz.DIFFICULTY_CHOICES:
        levels.append(
            {
                "value": value,
                "label": label,
                "quiz_count": Quiz.objects.filter(difficulty=value).count(),
                "active_count": Quiz.objects.filter(difficulty=value, status="active").count(),
            }
        )
    return render(request, "admin_test_levels.html", {"levels": levels})


@admin_required
def admin_manage_results(request):
    attempts = Attempt.objects.select_related("user", "quiz", "quiz__company").order_by(
        "-completed_at"
    )

    company_id = (request.GET.get("company") or "").strip()
    if company_id.isdigit():
        attempts = attempts.filter(quiz__company_id=int(company_id))

    difficulty = (request.GET.get("level") or "").strip()
    if difficulty in dict(Quiz.DIFFICULTY_CHOICES):
        attempts = attempts.filter(quiz__difficulty=difficulty)

    student_query = (request.GET.get("student") or "").strip()
    if student_query:
        attempts = attempts.filter(
            Q(user__username__icontains=student_query) | Q(user__email__icontains=student_query)
        )

    companies = Company.objects.filter(is_active=True).order_by("name")
    return render(
        request,
        "admin_results.html",
        {
            "attempts": attempts[:200],
            "companies": companies,
            "selected_company": company_id,
            "selected_level": difficulty,
            "student_query": student_query,
            "difficulty_choices": Quiz.DIFFICULTY_CHOICES,
        },
    )


# ═══════════════════════════════════════════════════════════════
# FULL ADMIN CONTROL — additional views
# ═══════════════════════════════════════════════════════════════


@admin_required
@require_POST
def admin_delete_attempt(request, attempt_id):
    """Delete a student's test attempt (result)."""
    attempt = get_object_or_404(Attempt, pk=attempt_id)
    attempt.delete()
    messages.success(request, "Attempt deleted.")
    return redirect("admin_manage_results")


@admin_required
def admin_student_detail(request, user_id):
    """View a specific student's profile + full history."""
    student = get_object_or_404(User, pk=user_id, is_staff=False)
    attempts = (
        Attempt.objects.filter(user=student)
        .select_related("quiz", "quiz__company")
        .order_by("-completed_at")
    )
    from django.db.models import Sum, Avg
    stats = attempts.aggregate(
        attempt_count=Count("id"),
        avg_score=Avg("marks_obtained"),
        total_correct=Sum("correct_count"),
        total_questions=Sum("total"),
    )
    accuracy = 0
    if stats["total_questions"] and stats["total_questions"] > 0:
        accuracy = round((stats["total_correct"] or 0) / stats["total_questions"] * 100)
    return render(request, "admin_student_detail.html", {
        "student": student,
        "attempts": attempts,
        "stats": stats,
        "accuracy": accuracy,
    })


@admin_required
def admin_reset_password(request, user_id):
    """Reset a student's password from admin panel."""
    student = get_object_or_404(User, pk=user_id, is_staff=False)
    if request.method == "POST":
        new_password = request.POST.get("password") or ""
        if not new_password:
            messages.error(request, "Password is required.")
            return redirect("admin_reset_password", user_id=student.id)
        try:
            validate_password(new_password, user=student)
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return redirect("admin_reset_password", user_id=student.id)
        student.set_password(new_password)
        student.save()
        messages.success(request, f"Password reset for {student.username}.")
        return redirect("admin_student_detail", user_id=student.id)
    return render(request, "admin_reset_password.html", {"student": student})


@admin_required
def admin_manage_categories(request):
    """List all quiz categories."""
    categories = Category.objects.annotate(quiz_count=Count("quizzes")).order_by("name")
    return render(request, "admin_categories.html", {"categories": categories})


@admin_required
def admin_add_category(request):
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()[:100]
        if not name:
            messages.error(request, "Category name is required.")
            return redirect("admin_add_category")
        if Category.objects.filter(name__iexact=name).exists():
            messages.error(request, "A category with this name already exists.")
            return redirect("admin_add_category")
        Category.objects.create(name=name)
        messages.success(request, "Category created.")
        return redirect("admin_manage_categories")
    return render(request, "admin_add_category.html")


@admin_required
def admin_edit_category(request, category_id):
    category = get_object_or_404(Category, pk=category_id)
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()[:100]
        if not name:
            messages.error(request, "Category name is required.")
            return redirect("admin_edit_category", category_id=category.id)
        if Category.objects.filter(name__iexact=name).exclude(pk=category.pk).exists():
            messages.error(request, "A category with this name already exists.")
            return redirect("admin_edit_category", category_id=category.id)
        category.name = name
        image = request.FILES.get("image")
        if image:
            category.image = image
        category.save()
        messages.success(request, "Category updated.")
        return redirect("admin_manage_categories")
    return render(request, "admin_add_category.html", {"category": category})


@admin_required
@require_POST
def admin_delete_category(request, category_id):
    category = get_object_or_404(Category, pk=category_id)
    if category.quizzes.exists():
        messages.error(request, "Cannot delete a category that still has quizzes. Reassign them first.")
        return redirect("admin_manage_categories")
    category.delete()
    messages.success(request, "Category deleted.")
    return redirect("admin_manage_categories")


@admin_required
@require_POST
def admin_bulk_delete_users(request):
    """Bulk delete selected students."""
    user_ids = request.POST.getlist("user_ids")
    if not user_ids:
        messages.error(request, "No users selected.")
        return redirect("admin_manage_users")
    # Never delete staff/superuser via bulk action.
    deleted, _ = User.objects.filter(pk__in=user_ids, is_staff=False).exclude(pk=request.user.pk).delete()
    messages.success(request, f"Deleted {deleted} user(s).")
    return redirect("admin_manage_users")


@admin_required
@require_POST
def admin_bulk_quiz_status(request):
    """Bulk change quiz status (active/hold/disabled)."""
    quiz_ids = request.POST.getlist("quiz_ids")
    new_status = (request.POST.get("new_status") or "").strip()
    if not quiz_ids:
        messages.error(request, "No quizzes selected.")
        return redirect("admin_manage_quizzes")
    if new_status not in dict(Quiz.STATUS_CHOICES):
        messages.error(request, "Invalid status.")
        return redirect("admin_manage_quizzes")
    updated = Quiz.objects.filter(pk__in=quiz_ids).update(status=new_status)
    messages.success(request, f"Updated {updated} quiz(zes) to '{new_status}'.")
    return redirect("admin_manage_quizzes")


@admin_required
def admin_export_results(request):
    """Export filtered results as CSV download."""
    attempts = Attempt.objects.select_related("user", "quiz", "quiz__company").order_by("-completed_at")

    company_id = (request.GET.get("company") or "").strip()
    if company_id.isdigit():
        attempts = attempts.filter(quiz__company_id=int(company_id))

    difficulty = (request.GET.get("level") or "").strip()
    if difficulty in dict(Quiz.DIFFICULTY_CHOICES):
        attempts = attempts.filter(quiz__difficulty=difficulty)

    student_query = (request.GET.get("student") or "").strip()
    if student_query:
        attempts = attempts.filter(
            Q(user__username__icontains=student_query) | Q(user__email__icontains=student_query)
        )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="results_export.csv"'
    writer = csv.writer(response)
    writer.writerow(["Student Name", "Username", "Email", "Test", "Company", "Level", "Score", "Total", "Marks", "Completed"])
    for a in attempts[:5000]:
        writer.writerow([
            a.user.first_name or "—",
            a.user.username,
            a.user.email or "—",
            a.quiz.title,
            a.quiz.company.name if a.quiz.company else "—",
            a.quiz.get_difficulty_display(),
            a.score,
            a.total,
            str(a.marks_obtained),
            a.completed_at.strftime("%Y-%m-%d %H:%M") if a.completed_at else "",
        ])
    return response


@admin_required
def admin_site_settings(request):
    """View/info page for admin-controllable site settings (environment-based)."""
    if request.method == "POST":
        messages.info(request, "Settings are controlled via environment variables. Update your .env file and restart the server.")
        return redirect("admin_site_settings")
    current = {
        "quiz_question_seconds": settings.QUIZ_QUESTION_SECONDS,
        "register_max_per_ip": _REGISTER_MAX_PER_IP,
        "register_throttle_seconds": _REGISTER_THROTTLE_SECONDS,
        "session_cookie_age": getattr(settings, "SESSION_COOKIE_AGE", "N/A"),
        "debug": settings.DEBUG,
        "allowed_hosts": settings.ALLOWED_HOSTS,
        "openai_configured": bool(getattr(settings, "OPENAI_API_KEY", "")),
    }
    return render(request, "admin_site_settings.html", {"current": current})

