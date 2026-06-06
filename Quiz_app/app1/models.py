from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class Register(models.Model):
    """Legacy model — do not use for auth. Passwords here are stored in plain text."""

    username = models.CharField(max_length=30)
    Email = models.CharField(max_length=34)
    password = models.CharField(max_length=12)
    Conf_pass = models.CharField(max_length=12)

    def __str__(self):
        return self.username


class Company(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    logo = models.ImageField(upload_to="company_logos/", blank=True, null=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

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
