"""
Super-Admin-only views: Teacher management and audit logs.

Creating a Teacher sets is_staff=True, is_superuser=False and adds the user to
the Teacher group. Teachers can never be promoted to Super Admin here, and
these views are guarded by ``superadmin_required``.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .decorators import superadmin_required
from .forms import TeacherCreateForm, TeacherEditForm
from .models import AuditLog, TeacherProfile
from .permissions import TEACHER_GROUP
from .services import log_action


def _teacher_group():
    group, _ = Group.objects.get_or_create(name=TEACHER_GROUP)
    return group


@superadmin_required
def admin_teachers(request):
    teachers = (TeacherProfile.objects.select_related("user")
                .order_by("-created_at"))
    return render(request, "admin/teachers.html", {"teachers": teachers})


@superadmin_required
def admin_add_teacher(request):
    if request.method == "POST":
        form = TeacherCreateForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            with transaction.atomic():
                user = User.objects.create_user(
                    username=cd["username"],
                    email=cd["email"],
                    password=cd["password"],
                    first_name=cd["first_name"],
                    last_name=cd["last_name"],
                )
                # Teacher role: staff, never superuser.
                user.is_staff = True
                user.is_superuser = False
                user.is_active = cd["is_active"]
                user.save(update_fields=["is_staff", "is_superuser", "is_active"])
                user.groups.add(_teacher_group())
                TeacherProfile.objects.create(
                    user=user,
                    employee_id=cd["employee_id"],
                    department=cd["department"],
                    phone=cd["phone"],
                    is_active=cd["is_active"],
                )
            log_action(request, "teacher.create", model_name="User", object_id=user.pk,
                       description=f"Created teacher {user.username}")
            messages.success(request, f"Teacher {user.username} created.")
            return redirect("admin_teachers")
    else:
        form = TeacherCreateForm()
    return render(request, "admin/teacher_form.html", {"form": form, "mode": "add"})


@superadmin_required
def admin_edit_teacher(request, teacher_id):
    profile = get_object_or_404(TeacherProfile.objects.select_related("user"), pk=teacher_id)
    user = profile.user
    if request.method == "POST":
        form = TeacherEditForm(request.POST, user=user)
        if form.is_valid():
            cd = form.cleaned_data
            user.first_name = cd["first_name"]
            user.last_name = cd["last_name"]
            user.email = cd["email"]
            user.is_active = cd["is_active"]
            user.save(update_fields=["first_name", "last_name", "email", "is_active"])
            profile.employee_id = cd["employee_id"]
            profile.department = cd["department"]
            profile.phone = cd["phone"]
            profile.is_active = cd["is_active"]
            profile.save()
            log_action(request, "teacher.update", model_name="User", object_id=user.pk,
                       description=f"Edited teacher {user.username}")
            messages.success(request, "Teacher updated.")
            return redirect("admin_teachers")
    else:
        form = TeacherEditForm(user=user, initial={
            "first_name": user.first_name, "last_name": user.last_name,
            "email": user.email, "employee_id": profile.employee_id,
            "department": profile.department, "phone": profile.phone,
            "is_active": profile.is_active,
        })
    return render(request, "admin/teacher_form.html",
                  {"form": form, "mode": "edit", "teacher": profile})


@superadmin_required
@require_POST
def admin_deactivate_teacher(request, teacher_id):
    profile = get_object_or_404(TeacherProfile.objects.select_related("user"), pk=teacher_id)
    user = profile.user
    profile.is_active = not profile.is_active
    profile.save(update_fields=["is_active"])
    user.is_active = profile.is_active
    user.save(update_fields=["is_active"])
    log_action(request, "teacher.toggle_active", model_name="User", object_id=user.pk,
               description=f"Set teacher {user.username} active={profile.is_active}")
    messages.success(request, f"Teacher {'activated' if profile.is_active else 'deactivated'}.")
    return redirect("admin_teachers")


@superadmin_required
@require_POST
def admin_reset_teacher_password(request, teacher_id):
    profile = get_object_or_404(TeacherProfile.objects.select_related("user"), pk=teacher_id)
    user = profile.user
    new_password = request.POST.get("new_password") or ""
    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("admin_edit_teacher", teacher_id=profile.pk)
    user.set_password(new_password)
    user.save(update_fields=["password"])
    # Never log the password value.
    log_action(request, "teacher.reset_password", model_name="User", object_id=user.pk,
               description=f"Reset password for teacher {user.username}")
    messages.success(request, "Teacher password reset.")
    return redirect("admin_edit_teacher", teacher_id=profile.pk)


@superadmin_required
def admin_audit_logs(request):
    q = (request.GET.get("q") or "").strip()
    logs = AuditLog.objects.select_related("user").order_by("-created_at")
    if q:
        logs = logs.filter(
            Q(action__icontains=q) | Q(description__icontains=q)
            | Q(model_name__icontains=q) | Q(user__username__icontains=q)
        )
    return render(request, "admin/audit_logs.html", {"logs": logs[:500], "q": q})
