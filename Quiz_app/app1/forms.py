"""
Whitelisted ModelForms for teacher/admin management.

Security: fields are always listed explicitly (never ``fields="__all__"``) to
prevent mass-assignment / privilege escalation. Forms that touch a Django User
never expose is_staff, is_superuser, groups, permissions, password hash or
Supabase identifiers.
"""
from __future__ import annotations

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import Category, Company, PlacementDrive, Question, Quiz, StudentProfile, TeacherProfile


_TEXT = {"class": "form-control"}
_SELECT = {"class": "form-select"}
_CHECK = {"class": "form-check-input"}
_DATE = {"class": "form-control", "type": "date"}


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name", "logo", "description", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs=_TEXT),
            "description": forms.Textarea(attrs={**_TEXT, "rows": 3}),
            "is_active": forms.CheckboxInput(attrs=_CHECK),
        }


class PlacementDriveForm(forms.ModelForm):
    class Meta:
        model = PlacementDrive
        fields = [
            "company", "title", "job_role", "drive_date", "registration_deadline",
            "eligibility", "description", "location", "status",
        ]
        widgets = {
            "company": forms.Select(attrs=_SELECT),
            "title": forms.TextInput(attrs=_TEXT),
            "job_role": forms.TextInput(attrs=_TEXT),
            "drive_date": forms.DateInput(attrs=_DATE),
            "registration_deadline": forms.DateInput(attrs=_DATE),
            "eligibility": forms.Textarea(attrs={**_TEXT, "rows": 2}),
            "description": forms.Textarea(attrs={**_TEXT, "rows": 3}),
            "location": forms.TextInput(attrs=_TEXT),
            "status": forms.Select(attrs=_SELECT),
        }

    def clean(self):
        cleaned = super().clean()
        drive_date = cleaned.get("drive_date")
        deadline = cleaned.get("registration_deadline")
        if drive_date and deadline and deadline > drive_date:
            self.add_error("registration_deadline",
                           "Registration deadline cannot be after the drive date.")
        return cleaned


class QuizForm(forms.ModelForm):
    class Meta:
        model = Quiz
        fields = [
            "title", "category", "company", "placement_drive", "difficulty",
            "section", "duration_minutes", "marks_per_question", "negative_marks",
            "target_question_count", "status",
        ]
        widgets = {
            "title": forms.TextInput(attrs=_TEXT),
            "category": forms.Select(attrs=_SELECT),
            "company": forms.Select(attrs=_SELECT),
            "placement_drive": forms.Select(attrs=_SELECT),
            "difficulty": forms.Select(attrs=_SELECT),
            "section": forms.Select(attrs=_SELECT),
            "duration_minutes": forms.NumberInput(attrs=_TEXT),
            "marks_per_question": forms.NumberInput(attrs={**_TEXT, "step": "0.01"}),
            "negative_marks": forms.NumberInput(attrs={**_TEXT, "step": "0.01"}),
            "target_question_count": forms.NumberInput(attrs=_TEXT),
            "status": forms.Select(attrs=_SELECT),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].required = False
        self.fields["placement_drive"].required = False


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ["text", "explanation", "topic"]
        widgets = {
            "text": forms.Textarea(attrs={**_TEXT, "rows": 3}),
            "explanation": forms.Textarea(attrs={**_TEXT, "rows": 2}),
            "topic": forms.TextInput(attrs=_TEXT),
        }


class StudentEditForm(forms.Form):
    """Teacher-safe student editing.

    Only these whitelisted fields may ever be edited. is_staff, is_superuser,
    groups, permissions, password and Supabase identifiers are intentionally
    absent and can never be submitted through this form.
    """
    first_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=_TEXT))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=_TEXT))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs=_TEXT))
    roll_number = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs=_TEXT))
    college = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs=_TEXT))
    branch = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs=_TEXT))
    semester = forms.IntegerField(required=False, min_value=1, max_value=12,
                                  widget=forms.NumberInput(attrs=_TEXT))
    batch = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs=_TEXT))
    phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs=_TEXT))
    is_active = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=_CHECK))

    def __init__(self, *args, user=None, **kwargs):
        self._user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        qs = User.objects.filter(email__iexact=email)
        if self._user is not None:
            qs = qs.exclude(pk=self._user.pk)
        if email and qs.exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email

    def clean_roll_number(self):
        roll = (self.cleaned_data.get("roll_number") or "").strip()
        if not roll:
            return ""
        qs = StudentProfile.objects.filter(roll_number__iexact=roll)
        if self._user is not None:
            qs = qs.exclude(user__pk=self._user.pk)
        if qs.exists():
            raise forms.ValidationError("This roll number is already assigned to another student.")
        return roll


class TeacherCreateForm(forms.Form):
    """Super-Admin-only teacher creation. Sets is_staff=True, is_superuser=False,
    adds the user to the Teacher group and creates a TeacherProfile."""
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs=_TEXT))
    email = forms.EmailField(widget=forms.EmailInput(attrs=_TEXT))
    first_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=_TEXT))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=_TEXT))
    employee_id = forms.CharField(max_length=50, widget=forms.TextInput(attrs=_TEXT))
    department = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs=_TEXT))
    phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs=_TEXT))
    password = forms.CharField(widget=forms.PasswordInput(attrs=_TEXT))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs=_TEXT))
    is_active = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs=_CHECK))

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_employee_id(self):
        emp = (self.cleaned_data.get("employee_id") or "").strip()
        if TeacherProfile.objects.filter(employee_id__iexact=emp).exists():
            raise forms.ValidationError("This employee ID is already in use.")
        return emp

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get("password")
        confirm = cleaned.get("confirm_password")
        if pw and confirm and pw != confirm:
            self.add_error("confirm_password", "Passwords do not match.")
        if pw:
            try:
                validate_password(pw)
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        return cleaned


class TeacherEditForm(forms.Form):
    """Edit an existing teacher's profile fields (Super Admin only)."""
    first_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=_TEXT))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=_TEXT))
    email = forms.EmailField(widget=forms.EmailInput(attrs=_TEXT))
    employee_id = forms.CharField(max_length=50, widget=forms.TextInput(attrs=_TEXT))
    department = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs=_TEXT))
    phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs=_TEXT))
    is_active = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=_CHECK))

    def __init__(self, *args, user=None, **kwargs):
        self._user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        qs = User.objects.filter(email__iexact=email)
        if self._user is not None:
            qs = qs.exclude(pk=self._user.pk)
        if qs.exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email

    def clean_employee_id(self):
        emp = (self.cleaned_data.get("employee_id") or "").strip()
        qs = TeacherProfile.objects.filter(employee_id__iexact=emp)
        if self._user is not None:
            qs = qs.exclude(user__pk=self._user.pk)
        if qs.exists():
            raise forms.ValidationError("This employee ID is already in use.")
        return emp


class CsvUploadForm(forms.Form):
    """Generic CSV upload with size/type guard (2MB max)."""
    MAX_BYTES = 2 * 1024 * 1024

    csv_file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".csv"})
    )

    def clean_csv_file(self):
        f = self.cleaned_data["csv_file"]
        name = (f.name or "").lower()
        if not name.endswith(".csv"):
            raise forms.ValidationError("Please upload a .csv file.")
        if f.size > self.MAX_BYTES:
            raise forms.ValidationError("File too large. Maximum size is 2MB.")
        return f
