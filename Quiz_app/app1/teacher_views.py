"""
Teacher portal views for PlacementIQ.

Every object-level route re-checks ownership via app1.permissions helpers so a
Teacher cannot bypass authorization by editing the URL (IDOR protection).
Super Admins pass all ownership checks and may use these pages too.
"""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import teacher_required
from .forms import (
    CompanyForm,
    CsvUploadForm,
    PlacementDriveForm,
    QuestionForm,
    QuizForm,
    StudentEditForm,
)
from .models import (
    Attempt,
    AuditLog,
    Category,
    Company,
    ImportBatch,
    ImportedResult,
    Option,
    PendingStudentProfile,
    PlacementDrive,
    Question,
    Quiz,
    StudentProfile,
)
from .permissions import (
    can_manage_company,
    can_manage_drive,
    can_manage_quiz,
    is_super_admin,
    is_teacher,
)
from .services import build_csv_response, build_error_csv, log_action, normalize_email
from .ai_service import generate_questions
from . import views as core_views

from django.contrib.auth import authenticate, login
from django.views.decorators.http import require_http_methods


# ---------------------------------------------------------------------------
# Teacher Login (separate from Super Admin)
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def teacher_login_view(request):
    """Dedicated teacher login. Only Teacher group users can log in here.
    Super Admins are directed to the admin login instead."""
    if request.user.is_authenticated:
        if is_teacher(request.user):
            return redirect("teacher_dashboard")
        if is_super_admin(request.user):
            messages.info(request, "Super Admins should use the Admin login.")
            return redirect("admin_login")
        return redirect("student_dashboard")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        user = authenticate(request, username=username, password=password)
        if user is not None and is_teacher(user):
            request.session.cycle_key()
            login(request, user)
            messages.success(request, f"Welcome, {user.first_name or user.username}!")
            return redirect("teacher_dashboard")

        if user is not None and is_super_admin(user):
            messages.error(request, "Super Admin accounts cannot use the Teacher login. Use Admin login.")
        elif user is not None and not user.is_staff:
            messages.error(request, "Student accounts cannot access the Teacher portal.")
        else:
            messages.error(request, "Invalid teacher credentials.")
        return render(request, "teacher/login.html", {"form_username": username})

    return render(request, "teacher/login.html")


# ---------------------------------------------------------------------------
# Ownership-scoped querysets
# ---------------------------------------------------------------------------

def _managed_company_qs(user):
    if is_super_admin(user):
        return Company.objects.all()
    return Company.objects.filter(
        Q(created_by=user) | Q(assigned_teachers=user)
    ).distinct()


def _managed_drive_qs(user):
    if is_super_admin(user):
        return PlacementDrive.objects.all()
    return PlacementDrive.objects.filter(
        Q(created_by=user)
        | Q(assigned_teachers=user)
        | Q(company__created_by=user)
        | Q(company__assigned_teachers=user)
    ).distinct()


def _managed_quiz_qs(user):
    if is_super_admin(user):
        return Quiz.objects.all()
    return Quiz.objects.filter(
        Q(created_by=user)
        | Q(assigned_teachers=user)
        | Q(placement_drive__created_by=user)
        | Q(placement_drive__assigned_teachers=user)
        | Q(company__created_by=user)
        | Q(company__assigned_teachers=user)
    ).distinct()


def _students_qs():
    from django.contrib.auth.models import User
    return User.objects.filter(is_staff=False)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@teacher_required
def teacher_dashboard(request):
    user = request.user
    managed_quizzes = _managed_quiz_qs(user)
    quiz_ids = list(managed_quizzes.values_list("id", flat=True))

    attempts = Attempt.objects.filter(quiz_id__in=quiz_ids)
    agg = attempts.aggregate(total_score=Sum("score"), total_total=Sum("total"), n=Count("id"))
    avg_pct = 0
    if agg["total_total"]:
        avg_pct = round((agg["total_score"] or 0) / agg["total_total"] * 100, 1)

    today = timezone.now().date()
    cards = {
        "total_students": _students_qs().count(),
        "active_companies": Company.objects.filter(is_active=True).count(),
        "upcoming_drives": _managed_drive_qs(user).filter(
            status="upcoming", drive_date__gte=today
        ).count(),
        "managed_quizzes": managed_quizzes.count(),
        "total_attempts": agg["n"] or 0,
        "avg_score": avg_pct,
    }

    recent_quizzes = managed_quizzes.select_related("company").order_by("-created_at")[:5]
    recent_attempts = (
        attempts.select_related("user", "quiz").order_by("-completed_at")[:5]
    )
    upcoming = (
        _managed_drive_qs(user)
        .filter(drive_date__gte=today)
        .select_related("company")
        .order_by("drive_date")[:5]
    )
    recent_imports = ImportBatch.objects.filter(uploaded_by=user).order_by("-created_at")[:5]

    return render(request, "teacher/dashboard.html", {
        "cards": cards,
        "recent_quizzes": recent_quizzes,
        "recent_attempts": recent_attempts,
        "upcoming_drives": upcoming,
        "recent_imports": recent_imports,
    })


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

@teacher_required
def teacher_companies(request):
    companies = Company.objects.all().order_by("name")
    manageable_ids = set(_managed_company_qs(request.user).values_list("id", flat=True))
    return render(request, "teacher/companies.html", {
        "companies": companies,
        "manageable_ids": manageable_ids,
        "can_manage_all": is_super_admin(request.user),
    })


@teacher_required
def teacher_add_company(request):
    if request.method == "POST":
        form = CompanyForm(request.POST, request.FILES)
        logo = request.FILES.get("logo")
        logo_err = core_views._validate_logo_upload(logo) if logo else None
        if logo_err:
            form.add_error("logo", logo_err)
        if form.is_valid():
            company = form.save(commit=False)
            company.created_by = request.user
            company.updated_by = request.user
            company.save()
            log_action(request, "company.create", model_name="Company",
                       object_id=company.pk, description=f"Created company {company.name}")
            messages.success(request, "Company created.")
            return redirect("teacher_companies")
    else:
        form = CompanyForm()
    return render(request, "teacher/company_form.html", {"form": form, "mode": "add"})


@teacher_required
def teacher_edit_company(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    if not can_manage_company(request.user, company):
        return HttpResponseForbidden("You cannot manage this company.")
    if request.method == "POST":
        form = CompanyForm(request.POST, request.FILES, instance=company)
        logo = request.FILES.get("logo")
        logo_err = core_views._validate_logo_upload(logo) if logo else None
        if logo_err:
            form.add_error("logo", logo_err)
        if form.is_valid():
            company = form.save(commit=False)
            company.updated_by = request.user
            company.save()
            log_action(request, "company.update", model_name="Company",
                       object_id=company.pk, description=f"Edited company {company.name}")
            messages.success(request, "Company updated.")
            return redirect("teacher_companies")
    else:
        form = CompanyForm(instance=company)
    return render(request, "teacher/company_form.html",
                  {"form": form, "mode": "edit", "company": company})


@teacher_required
@require_POST
def teacher_toggle_company(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    if not can_manage_company(request.user, company):
        return HttpResponseForbidden("You cannot manage this company.")
    company.is_active = not company.is_active
    company.updated_by = request.user
    company.save(update_fields=["is_active", "updated_by"])
    log_action(request, "company.toggle_active", model_name="Company",
               object_id=company.pk,
               description=f"Set {company.name} active={company.is_active}")
    messages.success(request, f"Company marked {'active' if company.is_active else 'inactive'}.")
    return redirect("teacher_companies")


# ---------------------------------------------------------------------------
# Placement drives
# ---------------------------------------------------------------------------

@teacher_required
def teacher_drives(request):
    drives = PlacementDrive.objects.select_related("company").order_by("-drive_date")
    manageable_ids = set(_managed_drive_qs(request.user).values_list("id", flat=True))
    return render(request, "teacher/drives.html", {
        "drives": drives,
        "manageable_ids": manageable_ids,
    })


@teacher_required
def teacher_add_drive(request):
    if request.method == "POST":
        form = PlacementDriveForm(request.POST)
        if form.is_valid():
            drive = form.save(commit=False)
            drive.created_by = request.user
            drive.updated_by = request.user
            drive.save()
            log_action(request, "drive.create", model_name="PlacementDrive",
                       object_id=drive.pk, description=f"Created drive {drive.title}")
            messages.success(request, "Placement drive created.")
            return redirect("teacher_drives")
    else:
        form = PlacementDriveForm()
    return render(request, "teacher/drive_form.html", {"form": form, "mode": "add"})


@teacher_required
def teacher_edit_drive(request, drive_id):
    drive = get_object_or_404(PlacementDrive, pk=drive_id)
    if not can_manage_drive(request.user, drive):
        return HttpResponseForbidden("You cannot manage this drive.")
    if request.method == "POST":
        form = PlacementDriveForm(request.POST, instance=drive)
        if form.is_valid():
            drive = form.save(commit=False)
            drive.updated_by = request.user
            drive.save()
            log_action(request, "drive.update", model_name="PlacementDrive",
                       object_id=drive.pk, description=f"Edited drive {drive.title}")
            messages.success(request, "Drive updated.")
            return redirect("teacher_drives")
    else:
        form = PlacementDriveForm(instance=drive)
    return render(request, "teacher/drive_form.html",
                  {"form": form, "mode": "edit", "drive": drive})


# ---------------------------------------------------------------------------
# Quizzes
# ---------------------------------------------------------------------------

@teacher_required
def teacher_quizzes(request):
    quizzes = (
        _managed_quiz_qs(request.user)
        .select_related("company", "placement_drive")
        .annotate(qcount=Count("question", distinct=True),
                  attempt_count=Count("attempt", distinct=True))
        .order_by("-created_at")
    )
    return render(request, "teacher/quizzes.html", {"quizzes": quizzes})


def _quiz_form_context(form):
    return {
        "form": form,
        "categories": Category.objects.all().order_by("name"),
        "companies": Company.objects.filter(is_active=True).order_by("name"),
        "drives": PlacementDrive.objects.select_related("company").order_by("-drive_date"),
    }


@teacher_required
def teacher_add_quiz(request):
    if request.method == "POST":
        form = QuizForm(request.POST)
        if form.is_valid():
            quiz = form.save(commit=False)
            if not quiz.category_id:
                quiz.category = Category.objects.order_by("id").first()
            quiz.created_by = request.user
            quiz.updated_by = request.user
            quiz.save()
            form.save_m2m()
            log_action(request, "quiz.create", model_name="Quiz",
                       object_id=quiz.pk, description=f"Created quiz {quiz.title}")
            messages.success(request, "Quiz created. Add questions or generate with AI.")
            return redirect("teacher_quiz_questions", quiz_id=quiz.pk)
    else:
        form = QuizForm()
    ctx = _quiz_form_context(form)
    ctx["mode"] = "add"
    return render(request, "teacher/quiz_form.html", ctx)


@teacher_required
def teacher_edit_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id)
    if not can_manage_quiz(request.user, quiz):
        return HttpResponseForbidden("You cannot manage this quiz.")
    if request.method == "POST":
        form = QuizForm(request.POST, instance=quiz)
        if form.is_valid():
            quiz = form.save(commit=False)
            quiz.updated_by = request.user
            quiz.save()
            form.save_m2m()
            log_action(request, "quiz.update", model_name="Quiz",
                       object_id=quiz.pk, description=f"Edited quiz {quiz.title}")
            messages.success(request, "Quiz updated.")
            return redirect("teacher_quizzes")
    else:
        form = QuizForm(instance=quiz)
    ctx = _quiz_form_context(form)
    ctx["mode"] = "edit"
    ctx["quiz"] = quiz
    return render(request, "teacher/quiz_form.html", ctx)


@teacher_required
@require_POST
def teacher_delete_quiz(request, quiz_id):
    """Hard delete when the quiz has no results; otherwise archive it so all
    historical attempts/imported results and analytics are preserved."""
    quiz = get_object_or_404(Quiz, pk=quiz_id)
    if not can_manage_quiz(request.user, quiz):
        return HttpResponseForbidden("You cannot manage this quiz.")

    has_attempts = Attempt.objects.filter(quiz=quiz).exists()
    has_imported = ImportedResult.objects.filter(quiz=quiz).exists()

    if has_attempts or has_imported:
        quiz.is_archived = True
        quiz.archived_at = timezone.now()
        quiz.archived_by = request.user
        quiz.status = "disabled"
        quiz.save(update_fields=["is_archived", "archived_at", "archived_by", "status"])
        log_action(request, "quiz.archive", model_name="Quiz", object_id=quiz.pk,
                   description=f"Archived quiz {quiz.title} (has results)")
        messages.warning(
            request,
            "This quiz has student results, so it was archived instead of permanently deleted.",
        )
    else:
        title = quiz.title
        pk = quiz.pk
        quiz.delete()
        log_action(request, "quiz.delete", model_name="Quiz", object_id=pk,
                   description=f"Deleted quiz {title} (no results)")
        messages.success(request, "Quiz deleted.")
    return redirect("teacher_quizzes")


def _get_manageable_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id)
    if not can_manage_quiz(request.user, quiz):
        return None, HttpResponseForbidden("You cannot manage this quiz.")
    return quiz, None


@teacher_required
def teacher_quiz_questions(request, quiz_id):
    quiz, forbidden = _get_manageable_quiz(request, quiz_id)
    if forbidden:
        return forbidden
    questions = quiz.question_set.prefetch_related("options").all()
    return render(request, "teacher/quiz_questions.html",
                  {"quiz": quiz, "questions": questions})


@teacher_required
def teacher_add_question(request, quiz_id):
    quiz, forbidden = _get_manageable_quiz(request, quiz_id)
    if forbidden:
        return forbidden
    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()
        if not text:
            messages.error(request, "Question text is required.")
            return redirect("teacher_add_question", quiz_id=quiz.pk)
        _, error = core_views._parse_question_options(request.POST)
        if error:
            messages.error(request, error)
            return redirect("teacher_add_question", quiz_id=quiz.pk)
        with transaction.atomic():
            question = Question.objects.create(
                quiz=quiz,
                text=text,
                explanation=(request.POST.get("explanation") or "").strip(),
                topic=(request.POST.get("topic") or "").strip(),
            )
            core_views._save_question_options(question, request.POST)
        log_action(request, "question.create", model_name="Question",
                   object_id=question.pk, description=f"Added question to quiz {quiz.pk}")
        messages.success(request, "Question added.")
        return redirect("teacher_quiz_questions", quiz_id=quiz.pk)

    option_slots, correct_slot = core_views._question_option_slots()
    return render(request, "teacher/question_form.html",
                  {"quiz": quiz, "option_slots": option_slots, "correct_slot": correct_slot})


@teacher_required
def teacher_edit_question(request, question_id):
    question = get_object_or_404(Question.objects.prefetch_related("options"), pk=question_id)
    quiz = question.quiz
    if not can_manage_quiz(request.user, quiz):
        return HttpResponseForbidden("You cannot manage this quiz.")
    options = list(question.options.all()[:4])
    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()
        if not text:
            messages.error(request, "Question text is required.")
            return redirect("teacher_edit_question", question_id=question.pk)
        _, error = core_views._parse_question_options(request.POST)
        if error:
            messages.error(request, error)
            return redirect("teacher_edit_question", question_id=question.pk)
        question.text = text
        question.explanation = (request.POST.get("explanation") or "").strip()
        question.topic = (request.POST.get("topic") or "").strip()
        question.save(update_fields=["text", "explanation", "topic"])
        core_views._save_question_options(question, request.POST, existing_options=options)
        log_action(request, "question.update", model_name="Question",
                   object_id=question.pk, description=f"Edited question {question.pk}")
        messages.success(request, "Question updated.")
        return redirect("teacher_quiz_questions", quiz_id=quiz.pk)

    option_slots, correct_slot = core_views._question_option_slots(question)
    return render(request, "teacher/question_form.html",
                  {"quiz": quiz, "question": question,
                   "option_slots": option_slots, "correct_slot": correct_slot})


@teacher_required
@require_POST
def teacher_delete_question(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    quiz = question.quiz
    if not can_manage_quiz(request.user, quiz):
        return HttpResponseForbidden("You cannot manage this quiz.")
    pk = question.pk
    question.delete()
    log_action(request, "question.delete", model_name="Question", object_id=pk,
               description=f"Deleted question {pk} from quiz {quiz.pk}")
    messages.success(request, "Question deleted.")
    return redirect("teacher_quiz_questions", quiz_id=quiz.pk)


@teacher_required
def teacher_ai_generate(request, quiz_id):
    quiz, forbidden = _get_manageable_quiz(request, quiz_id)
    if forbidden:
        return forbidden
    if request.method == "POST":
        try:
            count = int(request.POST.get("count") or quiz.target_question_count or 10)
        except (TypeError, ValueError):
            count = 10
        count = max(1, min(count, 20))
        company_name = quiz.company.name if quiz.company else "Campus Placement"
        items, source = generate_questions(company_name, quiz.section, quiz.difficulty, count)
        start_num = quiz.question_set.count() + 1
        core_views._save_ai_questions(quiz, items, start_number=start_num)
        log_action(request, "question.ai_generate", model_name="Quiz", object_id=quiz.pk,
                   description=f"AI-generated {len(items)} questions ({source})")
        label = "AI" if source == "ai" else "built-in question bank"
        messages.success(request, f"Generated {len(items)} questions via {label}.")
        return redirect("teacher_quiz_questions", quiz_id=quiz.pk)
    return render(request, "teacher/ai_generate.html", {"quiz": quiz})


@teacher_required
def teacher_upload_csv(request, quiz_id):
    """Upload questions to a quiz via CSV.

    Columns: text, option_1, option_2, option_3, option_4, correct_option,
    explanation, topic. correct_option is the 1-based index of the answer.
    """
    quiz, forbidden = _get_manageable_quiz(request, quiz_id)
    if forbidden:
        return forbidden
    if request.method == "POST":
        form = CsvUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "; ".join(form.errors.get("csv_file", ["Invalid file."])))
            return redirect("teacher_upload_csv", quiz_id=quiz.pk)
        try:
            data = TextIOWrapper(form.cleaned_data["csv_file"].file, encoding="utf-8")
            reader = csv.DictReader(data)
            rows = list(reader)
        except (UnicodeDecodeError, csv.Error):
            messages.error(request, "Could not read the CSV. Ensure it is valid UTF-8.")
            return redirect("teacher_upload_csv", quiz_id=quiz.pk)
        if len(rows) > 2000:
            messages.error(request, "Too many rows (max 2000).")
            return redirect("teacher_upload_csv", quiz_id=quiz.pk)

        created = 0
        with transaction.atomic():
            for row in rows:
                text = (row.get("text") or "").strip()
                opts = [(row.get(f"option_{i}") or "").strip() for i in range(1, 5)]
                opts = [o for o in opts if o]
                try:
                    correct = int(row.get("correct_option") or 0)
                except (TypeError, ValueError):
                    correct = 0
                if not text or len(opts) < 2 or not (1 <= correct <= len(opts)):
                    continue
                q = Question.objects.create(
                    quiz=quiz, text=text,
                    explanation=(row.get("explanation") or "").strip(),
                    topic=(row.get("topic") or "").strip(),
                )
                for idx, otext in enumerate(opts, start=1):
                    Option.objects.create(question=q, text=otext, is_correct=(idx == correct))
                created += 1
        log_action(request, "question.csv_import", model_name="Quiz", object_id=quiz.pk,
                   description=f"Imported {created} questions via CSV")
        messages.success(request, f"Imported {created} questions.")
        return redirect("teacher_quiz_questions", quiz_id=quiz.pk)
    return render(request, "teacher/upload_csv.html", {"quiz": quiz})


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------

@teacher_required
def teacher_students(request):
    q = (request.GET.get("q") or "").strip()
    branch = (request.GET.get("branch") or "").strip()
    batch = (request.GET.get("batch") or "").strip()
    status = (request.GET.get("status") or "").strip()

    students = _students_qs().select_related("student_profile").order_by("username")
    if q:
        students = students.filter(
            Q(username__icontains=q) | Q(email__icontains=q)
            | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(student_profile__roll_number__icontains=q)
        )
    if branch:
        students = students.filter(student_profile__branch__iexact=branch)
    if batch:
        students = students.filter(student_profile__batch__iexact=batch)
    if status == "active":
        students = students.filter(Q(student_profile__is_active=True) | Q(student_profile__isnull=True))
    elif status == "inactive":
        students = students.filter(student_profile__is_active=False)

    branches = (StudentProfile.objects.exclude(branch="")
                .values_list("branch", flat=True).distinct().order_by("branch"))
    batches = (StudentProfile.objects.exclude(batch="")
               .values_list("batch", flat=True).distinct().order_by("batch"))

    return render(request, "teacher/students.html", {
        "students": students[:500],
        "q": q, "branch": branch, "batch": batch, "status": status,
        "branches": branches, "batches": batches,
    })


@teacher_required
def teacher_student_detail(request, user_id):
    from django.contrib.auth.models import User
    student = get_object_or_404(_students_qs(), pk=user_id)
    profile = getattr(student, "student_profile", None)
    attempts = (Attempt.objects.filter(user=student)
                .select_related("quiz", "quiz__company").order_by("-completed_at"))
    imported = (ImportedResult.objects.filter(student=student)
                .select_related("quiz").order_by("-exam_date"))
    agg = attempts.aggregate(total_score=Sum("score"), total_total=Sum("total"), n=Count("id"))
    avg_pct = 0
    if agg["total_total"]:
        avg_pct = round((agg["total_score"] or 0) / agg["total_total"] * 100, 1)
    return render(request, "teacher/student_detail.html", {
        "student": student, "profile": profile,
        "attempts": attempts, "imported": imported,
        "total_attempts": agg["n"] or 0, "avg_score": avg_pct,
    })


@teacher_required
def teacher_student_edit(request, user_id):
    student = get_object_or_404(_students_qs(), pk=user_id)
    profile, _ = StudentProfile.objects.get_or_create(user=student)

    if request.method == "POST":
        form = StudentEditForm(request.POST, user=student)
        if form.is_valid():
            cd = form.cleaned_data
            student.first_name = cd["first_name"]
            student.last_name = cd["last_name"]
            student.email = cd["email"]
            student.save(update_fields=["first_name", "last_name", "email"])
            profile.roll_number = cd["roll_number"] or None
            profile.college = cd["college"]
            profile.branch = cd["branch"]
            profile.semester = cd["semester"]
            profile.batch = cd["batch"]
            profile.phone = cd["phone"]
            profile.is_active = cd["is_active"]
            profile.save()
            log_action(request, "student.update", model_name="User", object_id=student.pk,
                       description=f"Edited student {student.username}")
            messages.success(request, "Student profile updated.")
            return redirect("teacher_student_detail", user_id=student.pk)
    else:
        form = StudentEditForm(user=student, initial={
            "first_name": student.first_name,
            "last_name": student.last_name,
            "email": student.email,
            "roll_number": profile.roll_number or "",
            "college": profile.college,
            "branch": profile.branch,
            "semester": profile.semester,
            "batch": profile.batch,
            "phone": profile.phone,
            "is_active": profile.is_active,
        })
    return render(request, "teacher/student_edit.html", {"form": form, "student": student})


# ---------------------------------------------------------------------------
# Student CSV import / export
# ---------------------------------------------------------------------------

STUDENT_CSV_HEADER = [
    "email", "first_name", "last_name", "roll_number",
    "college", "branch", "semester", "batch", "phone",
]


@teacher_required
def teacher_import_students(request):
    from django.contrib.auth.models import User

    if request.method == "POST":
        form = CsvUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "; ".join(form.errors.get("csv_file", ["Invalid file."])))
            return redirect("teacher_import_students")
        try:
            data = TextIOWrapper(form.cleaned_data["csv_file"].file, encoding="utf-8")
            reader = csv.DictReader(data)
            rows = list(reader)
        except (UnicodeDecodeError, csv.Error):
            messages.error(request, "Could not read the CSV. Ensure it is valid UTF-8.")
            return redirect("teacher_import_students")

        if len(rows) > 2000:
            messages.error(request, "Too many rows (max 2000).")
            return redirect("teacher_import_students")

        seen_emails = set()
        valid, invalid = [], []
        for i, row in enumerate(rows, start=2):  # header is row 1
            email = normalize_email(row.get("email"))
            raw = [row.get(h, "") for h in STUDENT_CSV_HEADER]
            if not email or "@" not in email:
                invalid.append((raw, "Invalid or missing email"))
                continue
            if email in seen_emails:
                invalid.append((raw, "Duplicate email in file"))
                continue
            seen_emails.add(email)
            sem = (row.get("semester") or "").strip()
            sem_val = None
            if sem:
                try:
                    sem_val = int(sem)
                    if not (1 <= sem_val <= 12):
                        raise ValueError
                except ValueError:
                    invalid.append((raw, "Invalid semester (1-12)"))
                    continue
            valid.append({
                "email": email,
                "first_name": (row.get("first_name") or "").strip(),
                "last_name": (row.get("last_name") or "").strip(),
                "roll_number": (row.get("roll_number") or "").strip(),
                "college": (row.get("college") or "").strip(),
                "branch": (row.get("branch") or "").strip(),
                "semester": sem_val,
                "batch": (row.get("batch") or "").strip(),
                "phone": (row.get("phone") or "").strip(),
                "raw": raw,
            })

        if request.POST.get("confirm") == "1":
            batch = ImportBatch.objects.create(
                import_type="student", filename=form.cleaned_data["csv_file"].name,
                uploaded_by=request.user, total_rows=len(rows),
            )
            success = 0
            errors = list(invalid)
            with transaction.atomic():
                for item in valid:
                    try:
                        existing = User.objects.filter(email__iexact=item["email"]).first()
                        if existing and not existing.is_staff:
                            _apply_student_profile(existing, item)
                        elif existing and existing.is_staff:
                            errors.append((item["raw"], "Email belongs to a staff account"))
                            continue
                        else:
                            _upsert_pending_student(item, request.user)
                        success += 1
                    except Exception as exc:  # noqa: BLE001
                        errors.append((item["raw"], str(exc)))
            batch.successful_rows = success
            batch.failed_rows = len(errors)
            batch.status = "completed" if not errors else ("partial" if success else "failed")
            batch.completed_at = timezone.now()
            if errors:
                batch.error_report = build_error_csv(STUDENT_CSV_HEADER, errors)
            batch.save()
            log_action(request, "student.csv_import", model_name="ImportBatch",
                       object_id=batch.pk,
                       description=f"Imported students: {success} ok, {len(errors)} failed")
            messages.success(request, f"Import complete: {success} processed, {len(errors)} failed.")
            if errors:
                messages.warning(request, "Some rows failed. See Import History for the error report.")
            return redirect("teacher_import_history")

        # Preview stage
        existing_emails = set(
            User.objects.filter(email__in=[v["email"] for v in valid])
            .values_list("email", flat=True)
        )
        for v in valid:
            v["exists"] = v["email"] in {e.lower() for e in existing_emails}
        return render(request, "teacher/import_students.html", {
            "preview": True, "valid": valid, "invalid": invalid,
            "header": STUDENT_CSV_HEADER,
        })

    return render(request, "teacher/import_students.html", {"preview": False})


def _apply_student_profile(user, item):
    profile, _ = StudentProfile.objects.get_or_create(user=user)
    if item["first_name"]:
        user.first_name = item["first_name"]
    if item["last_name"]:
        user.last_name = item["last_name"]
    user.save(update_fields=["first_name", "last_name"])
    profile.roll_number = item["roll_number"] or profile.roll_number or None
    profile.college = item["college"] or profile.college
    profile.branch = item["branch"] or profile.branch
    profile.semester = item["semester"] or profile.semester
    profile.batch = item["batch"] or profile.batch
    profile.phone = item["phone"] or profile.phone
    profile.save()


def _upsert_pending_student(item, teacher):
    obj, _ = PendingStudentProfile.objects.update_or_create(
        email=item["email"],
        defaults={
            "first_name": item["first_name"],
            "last_name": item["last_name"],
            "roll_number": item["roll_number"],
            "college": item["college"],
            "branch": item["branch"],
            "semester": item["semester"],
            "batch": item["batch"],
            "phone": item["phone"],
            "imported_by": teacher,
        },
    )
    return obj


@teacher_required
def teacher_export_students(request):
    from django.contrib.auth.models import User
    students = (_students_qs().select_related("student_profile")
                .annotate(n_attempts=Count("attempt", distinct=True)).order_by("username"))
    header = [
        "email", "username", "first_name", "last_name", "roll_number", "college",
        "branch", "semester", "batch", "phone", "account_status",
        "registration_date", "total_attempts", "average_score",
    ]
    rows = []
    for s in students:
        p = getattr(s, "student_profile", None)
        agg = Attempt.objects.filter(user=s).aggregate(
            ts=Sum("score"), tt=Sum("total"))
        avg = round((agg["ts"] or 0) / agg["tt"] * 100, 1) if agg["tt"] else 0
        active = (p.is_active if p else True)
        rows.append([
            s.email, s.username, s.first_name, s.last_name,
            (p.roll_number if p else "") or "", (p.college if p else ""),
            (p.branch if p else ""), (p.semester if p else "") or "",
            (p.batch if p else ""), (p.phone if p else ""),
            "active" if active else "inactive",
            s.date_joined.strftime("%Y-%m-%d") if s.date_joined else "",
            s.n_attempts, avg,
        ])
    log_action(request, "student.export", model_name="User",
               description=f"Exported {len(rows)} students")
    return build_csv_response("students.csv", header, rows)


# ---------------------------------------------------------------------------
# Results: offline import + export
# ---------------------------------------------------------------------------

RESULT_IMPORT_HEADER = [
    "student_email", "quiz_id", "score", "total",
    "correct_count", "wrong_count", "marks_obtained", "exam_date",
]


@teacher_required
def teacher_import_results(request):
    from django.contrib.auth.models import User

    if request.method == "POST":
        form = CsvUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "; ".join(form.errors.get("csv_file", ["Invalid file."])))
            return redirect("teacher_import_results")
        try:
            data = TextIOWrapper(form.cleaned_data["csv_file"].file, encoding="utf-8")
            reader = csv.DictReader(data)
            rows = list(reader)
        except (UnicodeDecodeError, csv.Error):
            messages.error(request, "Could not read the CSV. Ensure it is valid UTF-8.")
            return redirect("teacher_import_results")
        if len(rows) > 2000:
            messages.error(request, "Too many rows (max 2000).")
            return redirect("teacher_import_results")

        valid, invalid = [], []
        for row in rows:
            raw = [row.get(h, "") for h in RESULT_IMPORT_HEADER]
            email = normalize_email(row.get("student_email"))
            student = User.objects.filter(email__iexact=email, is_staff=False).first()
            if not student:
                invalid.append((raw, "Student not found"))
                continue
            try:
                quiz = Quiz.objects.get(pk=int(row.get("quiz_id") or 0))
            except (ValueError, Quiz.DoesNotExist):
                invalid.append((raw, "Quiz not found"))
                continue
            if not can_manage_quiz(request.user, quiz):
                invalid.append((raw, "You cannot manage this quiz"))
                continue
            try:
                score = int(row.get("score") or 0)
                total = int(row.get("total") or 0)
                correct = int(row.get("correct_count") or 0)
                wrong = int(row.get("wrong_count") or 0)
                marks = Decimal(str(row.get("marks_obtained") or "0"))
            except (ValueError, InvalidOperation):
                invalid.append((raw, "Invalid numeric value"))
                continue
            if min(score, total, correct, wrong) < 0 or marks < 0:
                invalid.append((raw, "Negative values not allowed"))
                continue
            if score > total or (correct + wrong) > total:
                invalid.append((raw, "Score/counts exceed total"))
                continue
            exam_date = core_views._parse_drive_date(row.get("exam_date"))[0]
            if not exam_date:
                invalid.append((raw, "Invalid exam_date (YYYY-MM-DD)"))
                continue
            if ImportedResult.objects.filter(
                student=student, quiz=quiz, exam_date=exam_date, source="csv_import"
            ).exists():
                invalid.append((raw, "Duplicate offline result"))
                continue
            pct = round(score / total * 100, 2) if total else 0
            valid.append({
                "student": student, "quiz": quiz, "score": score, "total": total,
                "correct": correct, "wrong": wrong, "marks": marks,
                "exam_date": exam_date, "percentage": pct, "raw": raw,
            })

        if request.POST.get("confirm") == "1":
            batch = ImportBatch.objects.create(
                import_type="offline_result", filename=form.cleaned_data["csv_file"].name,
                uploaded_by=request.user, total_rows=len(rows),
            )
            success = 0
            errors = list(invalid)
            with transaction.atomic():
                for item in valid:
                    try:
                        ImportedResult.objects.create(
                            student=item["student"], quiz=item["quiz"],
                            score=item["score"], total=item["total"],
                            correct_count=item["correct"], wrong_count=item["wrong"],
                            marks_obtained=item["marks"], percentage=item["percentage"],
                            exam_date=item["exam_date"], source="csv_import",
                            import_batch=batch, created_by=request.user,
                        )
                        success += 1
                    except Exception as exc:  # noqa: BLE001
                        errors.append((item["raw"], str(exc)))
            batch.successful_rows = success
            batch.failed_rows = len(errors)
            batch.status = "completed" if not errors else ("partial" if success else "failed")
            batch.completed_at = timezone.now()
            if errors:
                batch.error_report = build_error_csv(RESULT_IMPORT_HEADER, errors)
            batch.save()
            log_action(request, "result.csv_import", model_name="ImportBatch",
                       object_id=batch.pk,
                       description=f"Imported offline results: {success} ok, {len(errors)} failed")
            messages.success(request, f"Import complete: {success} added, {len(errors)} failed.")
            return redirect("teacher_import_history")

        return render(request, "teacher/import_results.html", {
            "preview": True, "valid": valid, "invalid": invalid,
            "header": RESULT_IMPORT_HEADER,
        })

    return render(request, "teacher/import_results.html", {"preview": False})


@teacher_required
def teacher_results(request):
    company_id = (request.GET.get("company") or "").strip()
    quiz_id = (request.GET.get("quiz") or "").strip()
    result_type = (request.GET.get("type") or "").strip()

    attempts = (Attempt.objects.select_related("user", "quiz", "quiz__company")
                .order_by("-completed_at"))
    imported = (ImportedResult.objects.select_related("student", "quiz", "quiz__company")
                .order_by("-exam_date"))
    if company_id:
        attempts = attempts.filter(quiz__company_id=company_id)
        imported = imported.filter(quiz__company_id=company_id)
    if quiz_id:
        attempts = attempts.filter(quiz_id=quiz_id)
        imported = imported.filter(quiz_id=quiz_id)

    show_online = result_type in ("", "online")
    show_offline = result_type in ("", "offline")
    return render(request, "teacher/results.html", {
        "attempts": attempts[:300] if show_online else [],
        "imported": imported[:300] if show_offline else [],
        "companies": Company.objects.order_by("name"),
        "quizzes": Quiz.objects.order_by("title"),
        "company_id": company_id, "quiz_id": quiz_id, "result_type": result_type,
    })


@teacher_required
def teacher_export_results(request):
    company_id = (request.GET.get("company") or "").strip()
    quiz_id = (request.GET.get("quiz") or "").strip()
    result_type = (request.GET.get("type") or "").strip()

    header = [
        "result_type", "student_email", "student_name", "roll_number", "branch",
        "batch", "company", "drive", "quiz", "score", "total", "percentage",
        "correct_count", "wrong_count", "marks_obtained", "time_spent_seconds",
        "completed_or_exam_date",
    ]
    rows = []

    def profile_bits(user):
        p = getattr(user, "student_profile", None)
        return ((p.roll_number if p else "") or "", (p.branch if p else ""), (p.batch if p else ""))

    if result_type in ("", "online"):
        aqs = Attempt.objects.select_related(
            "user", "user__student_profile", "quiz", "quiz__company", "quiz__placement_drive")
        if company_id:
            aqs = aqs.filter(quiz__company_id=company_id)
        if quiz_id:
            aqs = aqs.filter(quiz_id=quiz_id)
        for a in aqs:
            roll, branch, batch = profile_bits(a.user)
            pct = round(a.score / a.total * 100, 2) if a.total else 0
            rows.append([
                "online", a.user.email, a.user.get_full_name() or a.user.username,
                roll, branch, batch,
                a.quiz.company.name if a.quiz.company else "",
                a.quiz.placement_drive.title if a.quiz.placement_drive else "",
                a.quiz.title, a.score, a.total, pct, a.correct_count, a.wrong_count,
                a.marks_obtained, a.time_spent_seconds,
                a.completed_at.strftime("%Y-%m-%d") if a.completed_at else "",
            ])

    if result_type in ("", "offline"):
        iqs = ImportedResult.objects.select_related(
            "student", "student__student_profile", "quiz", "quiz__company", "quiz__placement_drive")
        if company_id:
            iqs = iqs.filter(quiz__company_id=company_id)
        if quiz_id:
            iqs = iqs.filter(quiz_id=quiz_id)
        for r in iqs:
            roll, branch, batch = profile_bits(r.student)
            rows.append([
                "offline", r.student.email, r.student.get_full_name() or r.student.username,
                roll, branch, batch,
                r.quiz.company.name if r.quiz.company else "",
                r.quiz.placement_drive.title if r.quiz.placement_drive else "",
                r.quiz.title, r.score, r.total, r.percentage, r.correct_count,
                r.wrong_count, r.marks_obtained, "",
                r.exam_date.strftime("%Y-%m-%d") if r.exam_date else "",
            ])

    log_action(request, "result.export", model_name="Attempt",
               description=f"Exported {len(rows)} results")
    return build_csv_response("results.csv", header, rows)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@teacher_required
def teacher_analytics(request):
    user = request.user
    managed_quizzes = _managed_quiz_qs(user)
    quiz_ids = list(managed_quizzes.values_list("id", flat=True))
    attempts = Attempt.objects.filter(quiz_id__in=quiz_ids)

    agg = attempts.aggregate(ts=Sum("score"), tt=Sum("total"), n=Count("id"))
    avg_pct = round((agg["ts"] or 0) / agg["tt"] * 100, 1) if agg["tt"] else 0
    pass_count = sum(1 for a in attempts.only("score", "total")
                     if a.total and (a.score / a.total) >= 0.4)
    total_n = agg["n"] or 0
    pass_pct = round(pass_count / total_n * 100, 1) if total_n else 0

    top = (attempts.values("user__username", "user__first_name", "user__last_name")
           .annotate(avg=Avg("score"), attempts=Count("id"))
           .order_by("-avg")[:10])
    weak = (attempts.values("user__username", "user__first_name", "user__last_name")
            .annotate(avg=Avg("score"), attempts=Count("id"))
            .order_by("avg")[:10])
    by_company = (attempts.values("quiz__company__name")
                  .annotate(n=Count("id"), avg=Avg("score")).order_by("-n"))
    by_quiz = (attempts.values("quiz__title")
               .annotate(n=Count("id"), avg=Avg("score")).order_by("-n")[:20])
    by_section = (attempts.values("quiz__section")
                  .annotate(n=Count("id"), avg=Avg("score")).order_by("-n"))
    by_branch = (attempts.values("user__student_profile__branch")
                 .annotate(n=Count("id"), avg=Avg("score")).order_by("-n"))
    by_batch = (attempts.values("user__student_profile__batch")
                .annotate(n=Count("id"), avg=Avg("score")).order_by("-n"))
    avg_time = attempts.aggregate(t=Avg("time_spent_seconds"))["t"] or 0

    cards = {
        "total_students": _students_qs().count(),
        "active_students": StudentProfile.objects.filter(is_active=True).count(),
        "managed_quizzes": managed_quizzes.count(),
        "total_attempts": total_n,
        "imported_results": ImportedResult.objects.filter(quiz_id__in=quiz_ids).count(),
        "avg_pct": avg_pct,
        "pass_pct": pass_pct,
        "avg_time_min": round(avg_time / 60, 1),
    }
    return render(request, "teacher/analytics.html", {
        "cards": cards, "top": top, "weak": weak,
        "by_company": by_company, "by_quiz": by_quiz, "by_section": by_section,
        "by_branch": by_branch, "by_batch": by_batch,
    })


@teacher_required
def teacher_import_history(request):
    batches = ImportBatch.objects.filter(uploaded_by=request.user).order_by("-created_at")[:100]
    return render(request, "teacher/import_history.html", {"batches": batches})
