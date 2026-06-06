from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
import csv
import random
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .ai_service import explain_answer, generate_questions, number_question_items, personalized_suggestions
from .analytics import admin_placement_stats, student_section_performance, student_weak_topics, suggested_tests_for_user
from .models import Answer, Attempt, Category, Company, Option, Question, Quiz

_LOGIN_THROTTLE_SECONDS = 900
_LOGIN_MAX_PER_USERNAME = 5
_LOGIN_MAX_PER_IP = 25
_QUIZ_SUBMIT_LOCK_SECONDS = 120


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
    numbered = number_question_items(items, start=start_number)
    with transaction.atomic():
        for item in numbered:
            question = Question.objects.create(
                quiz=quiz,
                text=item["text"],
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


def _ensure_company_practice_quiz(company, section, difficulty):
    """Auto-create a practice test with AI questions when a company has none."""
    section = _valid_section(section)
    difficulty = _valid_difficulty(difficulty)
    section_label = dict(Quiz.SECTION_CHOICES).get(section, section.title())
    title = f"{company.name} — {section_label} ({difficulty.title()}) Practice"

    quiz = (
        Quiz.objects.filter(company=company, section=section, difficulty=difficulty, status="active")
        .annotate(qcount=Count("question"))
        .filter(qcount__gt=0)
        .first()
    )
    if quiz:
        return quiz

    quiz = Quiz.objects.filter(
        company=company, section=section, difficulty=difficulty, title=title
    ).first()
    if not quiz:
        quiz = Quiz.objects.create(
            title=title,
            company=company,
            section=section,
            difficulty=difficulty,
            category=_category_for_section(section),
            status="active",
            duration_minutes=30,
            target_question_count=10,
            marks_per_question=Decimal("1"),
            negative_marks=Decimal("0.25"),
        )

    if quiz.question_set.count() == 0:
        items, _ = generate_questions(company.name, section, difficulty, 10)
        _save_ai_questions(quiz, items, start_number=1)

    return quiz


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


def _register_throttle_key(request):
    return f"register_throttle:ip:{_client_ip(request)}"


def _is_register_throttled(request):
    return cache.get(_register_throttle_key(request), 0) >= settings.REGISTER_MAX_PER_IP


def _bump_register_throttle(request):
    key = _register_throttle_key(request)
    n = cache.get(key, 0) + 1
    cache.set(key, n, settings.REGISTER_THROTTLE_SECONDS)


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


def _quiz_attempt_context(request, quiz, current_question, options, question_index, question_ids, is_last_question):
    test_remaining = _test_seconds_remaining(request)
    return {
        "quiz": quiz,
        "question": current_question,
        "options": options,
        "question_number": question_index + 1,
        "total_questions": len(question_ids),
        "is_last_question": is_last_question,
        "seconds_remaining": test_remaining if test_remaining is not None else _seconds_remaining(request),
        "question_seconds": quiz.duration_minutes * 60 if test_remaining is not None else settings.QUIZ_QUESTION_SECONDS,
        "uses_test_timer": test_remaining is not None,
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


def _finish_or_advance_quiz(request, quiz, question_index, question_ids, is_last_question, question_id=None, option_id=None):
    if question_id is not None and option_id is not None:
        answers = request.session.get("answers", {})
        answers[str(question_id)] = option_id
        request.session["answers"] = answers

    if is_last_question:
        if not _acquire_quiz_submit_lock(request.user.pk, quiz.pk):
            messages.warning(request, "This quiz is already being submitted.")
            return redirect("my_attempts")
        try:
            attempt = _persist_quiz_attempt(request, quiz)
        finally:
            _release_quiz_submit_lock(request.user.pk, quiz.pk)
        return redirect("quiz_result", attempt_id=attempt.pk)

    request.session["question_index"] = question_index + 1
    return redirect("attempt_quiz", quiz_id=quiz.pk)


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        if _is_login_throttled(request, username):
            messages.error(
                request,
                "Too many login attempts. Please wait a few minutes and try again.",
            )
            return redirect("login")

        user = authenticate(request, username=username, password=password)
        if user is not None:
            _clear_login_throttle(request, username)
            request.session.cycle_key()
            login(request, user)
            messages.success(request, f"Welcome {username}!")
            return redirect("home")

        _bump_login_throttle(request, username)
        messages.error(request, "Invalid username or password.")
        return redirect("login")

    return render(request, "login.html")


@login_required
@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("login")


def home(request):
    companies = (
        Company.objects.filter(is_active=True)
        .annotate(test_count=Count("tests", filter=Q(tests__status="active")))
        .order_by("name")
    )
    upcoming_drives = (
        Quiz.objects.filter(status="active", drive_date__gte=date.today(), company__isnull=False)
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
    _ensure_company_practice_quiz(company, practice_section, practice_level)

    tests = (
        Quiz.objects.filter(company=company, status="active")
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
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        if _is_register_throttled(request):
            messages.error(
                request,
                "Too many registration attempts from this network. Please try again later.",
            )
            return redirect("register")

        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        confirm = request.POST.get("confirm_password") or ""

        if not username or not email:
            messages.error(request, "Username and email are required.")
            return redirect("register")

        if password != confirm:
            messages.error(request, "Passwords do not match.")
            return redirect("register")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("register")

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "Email already exists.")
            return redirect("register")

        try:
            validate_password(password, user=User(username=username, email=email))
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return redirect("register")

        User.objects.create_user(username=username, email=email, password=password)
        _bump_register_throttle(request)
        messages.success(request, "Account created successfully. Please login.")
        return redirect("login")

    return render(request, "register.html")


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


@login_required
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

    _init_quiz_attempt(request, quiz)
    return redirect("attempt_quiz", quiz_id=quiz.id)


@login_required
def attempt_quiz(request, quiz_id):
    blocked = _staff_quiz_blocked_response(request)
    if blocked:
        _clear_quiz_session(request)
        return blocked

    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.status != "active":
        messages.warning(request, "This quiz is not currently active.")
        return redirect("home")

    question_ids = request.session.get("question_ids")
    if request.session.get("quiz_id") != quiz.id or not _session_quiz_state_ok(quiz, question_ids):
        _init_quiz_attempt(request, quiz)
        question_ids = request.session.get("question_ids", [])

    question_index = request.session.get("question_index", 0)
    try:
        question_index = int(question_index)
    except (TypeError, ValueError):
        question_index = 0

    if _test_timed_out(request):
        messages.warning(request, "Test time is up. Submitting your answers.")
        if not _acquire_quiz_submit_lock(request.user.pk, quiz.pk):
            return redirect("my_attempts")
        try:
            attempt = _persist_quiz_attempt(request, quiz)
        finally:
            _release_quiz_submit_lock(request.user.pk, quiz.pk)
        return redirect("quiz_result", attempt_id=attempt.pk)

    if question_index >= len(question_ids):
        # Legacy or edge state: require explicit POST to finalize (no GET side effects).
        return render(request, "quiz_confirm_submit.html", {"quiz": quiz})

    current_question_id = question_ids[question_index]
    current_question = get_object_or_404(Question, id=current_question_id, quiz=quiz)
    options = current_question.options.all()
    is_last_question = question_index + 1 >= len(question_ids)

    if request.method == "POST":
        if _test_timed_out(request):
            messages.warning(request, "Test time is up. Submitting your answers.")
            return _finish_or_advance_quiz(
                request,
                quiz,
                question_index,
                question_ids,
                True,
            )

        if _question_timed_out(request):
            messages.warning(request, "Time is up. Moving to the next question.")
            return _finish_or_advance_quiz(
                request,
                quiz,
                question_index,
                question_ids,
                is_last_question,
            )

        selected_option_id = request.POST.get("option")
        if not selected_option_id:
            messages.error(request, "Please select an option.")
            _refresh_question_deadline(request)
            return render(
                request,
                "quiz_attempt.html",
                _quiz_attempt_context(
                    request, quiz, current_question, options, question_index, question_ids, is_last_question
                ),
            )

        try:
            selected_option_id = int(selected_option_id)
        except (TypeError, ValueError):
            messages.error(request, "Invalid option submitted.")
            _refresh_question_deadline(request)
            return render(
                request,
                "quiz_attempt.html",
                _quiz_attempt_context(
                    request, quiz, current_question, options, question_index, question_ids, is_last_question
                ),
            )

        try:
            selected_option = options.get(id=selected_option_id)
        except Option.DoesNotExist:
            messages.error(request, "Invalid option submitted.")
            _refresh_question_deadline(request)
            return render(
                request,
                "quiz_attempt.html",
                _quiz_attempt_context(
                    request, quiz, current_question, options, question_index, question_ids, is_last_question
                ),
            )

        return _finish_or_advance_quiz(
            request,
            quiz,
            question_index,
            question_ids,
            is_last_question,
            question_id=current_question.id,
            option_id=selected_option.id,
        )

    _refresh_question_deadline(request)
    return render(
        request,
        "quiz_attempt.html",
        _quiz_attempt_context(
            request, quiz, current_question, options, question_index, question_ids, is_last_question
        ),
    )


@login_required
@require_POST
def finalize_quiz(request):
    """POST-only: finish quiz when session was left in 'index >= len' state (e.g. older flow or refresh)."""
    blocked = _staff_quiz_blocked_response(request)
    if blocked:
        _clear_quiz_session(request)
        return blocked

    quiz_id = request.session.get("quiz_id")
    question_ids = request.session.get("question_ids")
    question_index = request.session.get("question_index", 0)
    try:
        question_index = int(question_index)
    except (TypeError, ValueError):
        question_index = 0

    if not quiz_id or not isinstance(question_ids, list) or not question_ids:
        messages.warning(request, "No active quiz session.")
        return redirect("home")

    quiz = get_object_or_404(Quiz, pk=quiz_id, status="active")

    if not _session_quiz_state_ok(quiz, question_ids):
        messages.warning(request, "Quiz session expired or was reset.")
        return redirect("home")

    if question_index < len(question_ids):
        messages.error(request, "You have not finished all questions yet.")
        return redirect("attempt_quiz", quiz_id=quiz.id)

    if not _acquire_quiz_submit_lock(request.user.pk, quiz.pk):
        messages.warning(request, "This quiz is already being submitted.")
        return redirect("my_attempts")
    try:
        attempt = _persist_quiz_attempt(request, quiz)
    finally:
        _release_quiz_submit_lock(request.user.pk, quiz.pk)
    return redirect("quiz_result", attempt_id=attempt.pk)


@login_required
def quiz_result(request, attempt_id):
    attempt = get_object_or_404(
        Attempt.objects.select_related("quiz", "quiz__company"),
        pk=attempt_id,
        user=request.user,
    )
    percentage = round((attempt.score / attempt.total) * 100) if attempt.total else 0
    answer_rows = []
    for ans in attempt.answer_set.select_related("question", "selected_option").prefetch_related(
        "question__options"
    ):
        correct_option = ans.question.options.filter(is_correct=True).first()
        explanation = ans.question.explanation
        if not explanation and correct_option:
            explanation = explain_answer(
                ans.question.text,
                correct_option.text,
                ans.selected_option.text,
            )
        answer_rows.append(
            {
                "question": ans.question,
                "selected": ans.selected_option,
                "correct_option": correct_option,
                "is_correct": ans.is_correct,
                "explanation": explanation,
            }
        )
    return render(
        request,
        "quiz_result.html",
        {
            "score": attempt.score,
            "total_questions": attempt.total,
            "quiz": attempt.quiz,
            "percentage": percentage,
            "attempt": attempt,
            "answer_rows": answer_rows,
            "wrong_count": attempt.wrong_count,
            "correct_count": attempt.correct_count,
        },
    )


@login_required
def my_attempts(request):
    attempts = (
        Attempt.objects.filter(user=request.user)
        .select_related("quiz", "quiz__company")
        .order_by("-completed_at")
    )
    return render(request, "my_attempts.html", {"attempts": attempts})


@login_required
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


@login_required
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


@staff_member_required
def admin_dashboard(request):
    placement = admin_placement_stats()
    context = {
        "total_users": User.objects.count(),
        "total_quizzes": Quiz.objects.count(),
        "total_attempts": Attempt.objects.count(),
        "total_companies": Company.objects.filter(is_active=True).count(),
        "top_quizzes": Quiz.objects.annotate(attempts=Count("attempt")).order_by("-attempts")[:5],
        "by_company": placement["by_company"],
        "recent_attempts": placement["recent_attempts"],
    }
    return render(request, "admin_dashboard.html", context)


@staff_member_required
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


@staff_member_required
def admin_manage_users(request):
    users = User.objects.all()
    return render(request, "admin_users.html", {"users": users})


@staff_member_required
def admin_manage_quizzes(request):
    quizzes = (
        Quiz.objects.select_related("company", "category")
        .annotate(question_count=Count("question"))
        .order_by("-created_at")
    )
    return render(request, "admin_quizzes.html", {"quizzes": quizzes})


@staff_member_required
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


@staff_member_required
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


@staff_member_required
@require_POST
def admin_delete_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    quiz.delete()
    messages.success(request, "Quiz deleted.")
    return redirect("admin_manage_quizzes")


@staff_member_required
def admin_quiz_questions(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    questions = quiz.question_set.prefetch_related("options").all()
    return render(
        request,
        "admin_quiz_questions.html",
        {"quiz": quiz, "questions": questions},
    )


@staff_member_required
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


@staff_member_required
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


@staff_member_required
@require_POST
def admin_delete_question(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    quiz_id = question.quiz_id
    question.delete()
    messages.success(request, "Question deleted.")
    return redirect("admin_quiz_questions", quiz_id=quiz_id)


@staff_member_required
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
        start_num = quiz.question_set.count() + 1
        _save_ai_questions(quiz, items, start_number=start_num)

        label = "AI" if source == "ai" else "built-in question bank (set OPENAI_API_KEY for live AI)"
        messages.success(request, f"Generated {len(items)} questions via {label}.")
        return redirect("admin_quiz_questions", quiz_id=quiz.id)

    return render(request, "admin_ai_generate.html", {"quiz": quiz})


@staff_member_required
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

        file_data = TextIOWrapper(csv_file.file, encoding="utf-8")
        reader = csv.DictReader(file_data)

        max_rows = 1000
        created = 0
        with transaction.atomic():
            for idx, row in enumerate(reader):
                if idx >= max_rows:
                    break

                title = (row.get("title") or "").strip()
                category_name = (row.get("category") or "").strip()
                status = _valid_quiz_status((row.get("status") or "active").strip())
                if not title or not category_name:
                    continue

                category, _ = Category.objects.get_or_create(name=category_name)
                Quiz.objects.create(title=title, category=category, status=status)
                created += 1

        messages.success(request, f"Quizzes uploaded successfully ({created} added).")
        return redirect("admin_manage_quizzes")

    return render(request, "admin_upload_quizzes.html")


@staff_member_required
def admin_add_user(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""

        if not username or not email:
            messages.error(request, "Username and email are required.")
            return redirect("admin_add_user")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("admin_add_user")

        try:
            validate_password(password, user=User(username=username, email=email))
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return redirect("admin_add_user")

        User.objects.create_user(username=username, email=email, password=password)
        messages.success(request, "User created successfully.")
        return redirect("admin_manage_users")

    return render(request, "admin_add_user.html")


@staff_member_required
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


@staff_member_required
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

        file_data = TextIOWrapper(csv_file.file, encoding="utf-8")
        reader = csv.DictReader(file_data)

        max_rows = 1000
        created = 0
        with transaction.atomic():
            for idx, row in enumerate(reader):
                if idx >= max_rows:
                    break

                username = (row.get("username") or "").strip()
                email = (row.get("email") or "").strip()
                password = row.get("password") or ""
                if not username or not email or not password:
                    continue

                if User.objects.filter(username=username).exists():
                    continue

                try:
                    validate_password(password, user=User(username=username, email=email))
                except ValidationError:
                    continue

                User.objects.create_user(username=username, email=email, password=password)
                created += 1

        messages.success(request, f"Users uploaded successfully ({created} added).")
        return redirect("admin_manage_users")

    return render(request, "admin_upload_users.html")


@staff_member_required
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
