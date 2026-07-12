from decimal import Decimal
import uuid

from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class SupabaseUserMapping(models.Model):
    """
    Links a Supabase Auth user (UUID) to a local Django User.
    The Django User exists as a shadow for FK relationships (Attempt, Answer, etc.).
    Supabase Auth is the source of truth for authentication.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="supabase_mapping")
    supabase_user_id = models.UUIDField(unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Supabase User Mapping"
        verbose_name_plural = "Supabase User Mappings"

    def __str__(self):
        return f"{self.user.username} → {self.supabase_user_id}"


class Company(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    logo = models.ImageField(upload_to="company_logos/", blank=True, null=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    # Ownership / assignment (nullable for backward compatibility with existing data).
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_companies",
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="updated_companies",
    )
    assigned_teachers = models.ManyToManyField(
        User, blank=True, related_name="assigned_companies",
    )

    class Meta:
        verbose_name_plural = "companies"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "company"
            slug = base
            n = 1
            while Company.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    image = models.ImageField(upload_to="quiz_images/", blank=True, null=True)

    def __str__(self):
        return self.name


class Quiz(models.Model):
    STATUS_CHOICES = (
        ("active", "Active"),
        ("hold", "Hold"),
        ("disabled", "Disabled"),
    )
    DIFFICULTY_CHOICES = (
        ("easy", "Easy"),
        ("medium", "Medium"),
        ("hard", "Hard"),
    )
    SECTION_CHOICES = (
        ("aptitude", "Aptitude"),
        ("reasoning", "Reasoning"),
        ("english", "English"),
        ("coding", "Coding"),
        ("technical", "Technical"),
    )

    title = models.CharField(max_length=255)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="quizzes")
    company = models.ForeignKey(
        Company, on_delete=models.SET_NULL, null=True, blank=True, related_name="tests"
    )
    difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES, default="medium")
    section = models.CharField(max_length=20, choices=SECTION_CHOICES, default="aptitude")
    duration_minutes = models.PositiveIntegerField(default=30, help_text="Total test duration")
    marks_per_question = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("1.00"))
    negative_marks = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    target_question_count = models.PositiveIntegerField(default=10)
    drive_date = models.DateField(null=True, blank=True, help_text="Upcoming campus drive date")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    # Placement drive linkage (new UI uses this; drive_date kept for backward compat).
    placement_drive = models.ForeignKey(
        "PlacementDrive", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quizzes",
    )

    # Ownership / assignment (nullable; created_by=None means Super-Admin-managed).
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_quizzes",
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="updated_quizzes",
    )
    assigned_teachers = models.ManyToManyField(
        User, blank=True, related_name="assigned_quizzes",
    )

    # Archive support (delete-with-history becomes archive instead of hard delete).
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="archived_quizzes",
    )

    def __str__(self):
        return self.title

    @property
    def is_placement_test(self):
        return self.company_id is not None


class Question(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE)
    text = models.TextField()
    explanation = models.TextField(blank=True)
    topic = models.CharField(max_length=120, blank=True, help_text="Topic tag for weak-area analytics")
    ai_generated = models.BooleanField(default=False)

    def __str__(self):
        return self.text


class Option(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.text} ({'Correct' if self.is_correct else 'Wrong'})"


class Attempt(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE)
    score = models.IntegerField()
    total = models.IntegerField()
    correct_count = models.PositiveIntegerField(default=0)
    wrong_count = models.PositiveIntegerField(default=0)
    marks_obtained = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    time_spent_seconds = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.quiz.title} ({self.score}/{self.total})"


class Answer(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_option = models.ForeignKey(Option, on_delete=models.CASCADE)
    is_correct = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "question"],
                name="unique_answer_per_question",
            )
        ]


# ---------------------------------------------------------------------------
# Role profiles
# ---------------------------------------------------------------------------

class TeacherProfile(models.Model):
    """Extra metadata for a Teacher (Django is_staff, in the Teacher group)."""
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="teacher_profile"
    )
    employee_id = models.CharField(max_length=50, unique=True)
    department = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["employee_id"]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.employee_id})"


class StudentProfile(models.Model):
    """Extended profile for a registered student. Authentication stays with
    Supabase Auth; this only stores placement metadata."""
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="student_profile"
    )
    roll_number = models.CharField(max_length=50, blank=True, null=True, unique=True)
    college = models.CharField(max_length=200, blank=True)
    branch = models.CharField(max_length=120, blank=True)
    semester = models.PositiveSmallIntegerField(null=True, blank=True)
    batch = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"StudentProfile<{self.user.username}>"


class PendingStudentProfile(models.Model):
    """Imported students who have not registered yet. Claimed on first Supabase/
    Google login by normalized email. Never store fake passwords here."""
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    roll_number = models.CharField(max_length=50, blank=True)
    college = models.CharField(max_length=200, blank=True)
    branch = models.CharField(max_length=120, blank=True)
    semester = models.PositiveSmallIntegerField(null=True, blank=True)
    batch = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    imported_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="imported_pending_students",
    )
    claimed_by = models.OneToOneField(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="claimed_pending_profile",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return f"Pending<{self.email}>"


# ---------------------------------------------------------------------------
# Placement drives
# ---------------------------------------------------------------------------

class PlacementDrive(models.Model):
    STATUS_CHOICES = (
        ("draft", "Draft"),
        ("upcoming", "Upcoming"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    )

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="drives"
    )
    title = models.CharField(max_length=200)
    job_role = models.CharField(max_length=150, blank=True)
    drive_date = models.DateField()
    registration_deadline = models.DateField(null=True, blank=True)
    eligibility = models.TextField(blank=True)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_drives",
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="updated_drives",
    )
    assigned_teachers = models.ManyToManyField(
        User, blank=True, related_name="assigned_drives",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-drive_date"]

    def __str__(self):
        return f"{self.title} @ {self.company.name}"


# ---------------------------------------------------------------------------
# Import batches, offline results, audit logs
# ---------------------------------------------------------------------------

class ImportBatch(models.Model):
    IMPORT_TYPES = (
        ("student", "Student"),
        ("offline_result", "Offline Result"),
        ("question", "Question"),
    )
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("partial", "Partial"),
    )

    import_type = models.CharField(max_length=20, choices=IMPORT_TYPES)
    filename = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="import_batches",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    total_rows = models.PositiveIntegerField(default=0)
    successful_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    error_report = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_import_type_display()} import #{self.pk}"


class ImportedResult(models.Model):
    """Offline/manual test results. Stored separately from online Attempt records
    so historical online data is never faked or overwritten."""
    student = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="imported_results"
    )
    quiz = models.ForeignKey(
        Quiz, on_delete=models.PROTECT, related_name="imported_results"
    )
    score = models.IntegerField(default=0)
    total = models.IntegerField(default=0)
    correct_count = models.PositiveIntegerField(default=0)
    wrong_count = models.PositiveIntegerField(default=0)
    marks_obtained = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    exam_date = models.DateField()
    source = models.CharField(max_length=50, default="csv_import")
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="imported_results",
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_imported_results",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-exam_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "quiz", "exam_date", "source"],
                name="unique_imported_result",
            )
        ]

    def __str__(self):
        return f"ImportedResult<{self.student.username} - {self.quiz.title}>"


class AuditLog(models.Model):
    """Records sensitive management actions. Never store passwords, tokens,
    secret keys or raw authentication codes here."""
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="audit_logs"
    )
    action = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} by {self.user_id} @ {self.created_at:%Y-%m-%d %H:%M}"
