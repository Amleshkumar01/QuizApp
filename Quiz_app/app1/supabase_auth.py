"""
Supabase Auth views for PlacementIQ.
Handles student registration, login, and logout via Supabase GoTrue.
Templates, URLs, and form fields remain unchanged.
"""

import logging
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .models import SupabaseUserMapping
from . import supabase_client as sb

logger = logging.getLogger(__name__)

# ─── Rate Limiting ───────────────────────────────────────────────────────────
_LOGIN_THROTTLE_SECONDS = 900  # 15 minutes
_LOGIN_MAX_ATTEMPTS = 5

_USERNAME_RE = re.compile(r'^[a-z0-9._]{3,30}$')


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return (request.META.get("REMOTE_ADDR") or "unknown").strip() or "unknown"


def _throttle_key(request, identifier):
    ip = _client_ip(request)
    ident = (identifier or "").lower()[:150]
    return f"supa_login:{ip}:{ident}"


def _is_throttled(request, identifier):
    return cache.get(_throttle_key(request, identifier), 0) >= _LOGIN_MAX_ATTEMPTS


def _bump_throttle(request, identifier):
    key = _throttle_key(request, identifier)
    try:
        cache.add(key, 0, _LOGIN_THROTTLE_SECONDS)
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, _LOGIN_THROTTLE_SECONDS)


def _clear_throttle(request, identifier):
    cache.delete(_throttle_key(request, identifier))


# ─── Shadow User Management ─────────────────────────────────────────────────

def _get_or_create_shadow_user(supabase_user_id, username, email, first_name="", last_name=""):
    """Get or create a Django User linked to the Supabase user."""
    try:
        mapping = SupabaseUserMapping.objects.select_related("user").get(
            supabase_user_id=supabase_user_id
        )
        user = mapping.user
        changed = False
        if user.email != email:
            user.email = email
            changed = True
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            changed = True
        if last_name and user.last_name != last_name:
            user.last_name = last_name
            changed = True
        if changed:
            user.save(update_fields=["email", "first_name", "last_name"])
        return user
    except SupabaseUserMapping.DoesNotExist:
        pass

    with transaction.atomic():
        django_username = username[:150]
        if User.objects.filter(username=django_username).exists():
            django_username = f"{django_username}_{str(supabase_user_id)[:8]}"

        user = User(
            username=django_username,
            email=email,
            first_name=first_name or "",
            last_name=last_name or "",
            is_staff=False,
            is_active=True,
        )
        user.set_unusable_password()
        user.save()

        SupabaseUserMapping.objects.create(
            user=user,
            supabase_user_id=supabase_user_id,
        )
    return user


# ─── Registration View ───────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def supabase_register_view(request):
    """Student registration via Supabase Auth."""
    if request.session.get("supabase_user_id"):
        return redirect("student_dashboard")

    form_data = {}
    if request.method == "POST":
        first_name = (request.POST.get("first_name") or "").strip()[:150]
        last_name = (request.POST.get("last_name") or "").strip()[:150]
        username = (request.POST.get("username") or "").strip().lower()
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""
        confirm_password = request.POST.get("confirm_password") or ""

        form_data = {
            "form_first_name": first_name,
            "form_last_name": last_name,
            "form_username": username,
            "form_email": email,
        }

        # ── Server-side validation ──
        errors = []
        if not first_name:
            errors.append("First name is required.")
        if not last_name:
            errors.append("Last name is required.")
        if not username:
            errors.append("Username is required.")
        elif not _USERNAME_RE.match(username):
            errors.append("Username must be 3-30 characters: lowercase letters, numbers, dots, underscores only.")
        if not email:
            errors.append("Email is required.")
        elif "@" not in email or "." not in email.split("@")[-1]:
            errors.append("Enter a valid email address.")
        if not password:
            errors.append("Password is required.")
        elif password != confirm_password:
            errors.append("Passwords do not match.")

        if not errors and password:
            try:
                validate_password(password, user=User(username=username, email=email))
            except ValidationError as e:
                errors.extend(e.messages)

        # Check username availability
        if not errors:
            try:
                available = sb.rpc("username_available", {"check_username": username})
                if available is False:
                    errors.append("This username is already taken.")
            except sb.SupabaseError as e:
                logger.warning("Username check failed: %s", e.message[:200])
            except Exception as e:
                logger.warning("Username check error: %s", type(e).__name__)

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "register.html", form_data)

        # ── Register with Supabase Auth ──
        try:
            response = sb.sign_up(
                email=email,
                password=password,
                user_metadata={
                    "first_name": first_name,
                    "last_name": last_name,
                    "username": username,
                },
            )

            if not response.user:
                messages.error(request, "An account with this email may already exist.")
                return render(request, "register.html", form_data)

            # Duplicate detection: user with no identities
            if response.user.identities is not None and len(response.user.identities) == 0:
                messages.error(request, "An account with this email already exists. Please sign in instead.")
                return render(request, "register.html", form_data)

            # Email confirmation required (no session)
            if not response.session:
                messages.success(
                    request,
                    "Account created! Please check your email to verify your address, then sign in."
                )
                return redirect("student_login")

            # Auto-confirmed: signup succeeded
            messages.success(request, f"Welcome to PlacementIQ, {first_name or username}! Your account is ready.")
            return redirect("student_login")

        except sb.SupabaseError as e:
            msg = e.message.lower()
            if "already registered" in msg or "already been registered" in msg or "user already registered" in msg:
                messages.error(request, "An account with this email already exists.")
            elif "rate limit" in msg or "rate_limit" in msg:
                messages.error(request, "Too many signup attempts. Please wait and try again.")
            elif "invalid" in msg and "email" in msg:
                messages.error(request, "This email address is not accepted. Please use a valid email.")
            else:
                logger.error("Signup error [%d]: %s", e.status, e.message[:200])
                messages.error(request, "Registration failed. Please try again.")
            return render(request, "register.html", form_data)

        except Exception as e:
            logger.error("Unexpected signup error: %s: %s", type(e).__name__, str(e)[:200])
            messages.error(request, "Registration failed. Please try again.")
            return render(request, "register.html", form_data)

    return render(request, "register.html", form_data)


# ─── Login View ──────────────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def supabase_login_view(request):
    """Student login via Supabase Auth. Accepts username or email."""
    if request.session.get("supabase_user_id"):
        return redirect("student_dashboard")

    if request.method == "POST":
        identifier = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        generic_error = "Invalid username/email or password."

        if not identifier or not password:
            messages.error(request, generic_error)
            return render(request, "login.html", {"form_username": identifier})

        if _is_throttled(request, identifier):
            messages.error(request, "Too many login attempts. Please wait 15 minutes.")
            return render(request, "login.html", {"form_username": identifier})

        # Resolve username → email (server-side only)
        login_email = None
        if "@" in identifier:
            login_email = identifier.lower()
        else:
            try:
                profiles = sb.table_select(
                    "profiles",
                    columns="id",
                    filters={"username": identifier.lower()},
                    limit=1,
                )
                if profiles:
                    profile_id = profiles[0]["id"]
                    user_info = sb.admin_get_user_by_id(profile_id)
                    if user_info:
                        login_email = user_info.email
            except Exception as e:
                logger.warning("Username resolution: %s", type(e).__name__)

            if not login_email:
                _bump_throttle(request, identifier)
                messages.error(request, generic_error)
                return render(request, "login.html", {"form_username": identifier})

        # Authenticate
        try:
            response = sb.sign_in_with_password(email=login_email, password=password)
            if not response.user or not response.session:
                _bump_throttle(request, identifier)
                messages.error(request, generic_error)
                return render(request, "login.html", {"form_username": identifier})
        except sb.SupabaseError as e:
            _bump_throttle(request, identifier)
            if "email not confirmed" in e.message.lower():
                messages.error(request, "Please verify your email address first.")
            else:
                messages.error(request, generic_error)
            return render(request, "login.html", {"form_username": identifier})
        except Exception as e:
            logger.error("Login error: %s", type(e).__name__)
            messages.error(request, generic_error)
            return render(request, "login.html", {"form_username": identifier})

        # Check profile is active + get role
        supabase_uid = response.user.id
        try:
            profiles = sb.table_select(
                "profiles",
                columns="is_active,role,username,first_name,last_name",
                filters={"id": supabase_uid},
                limit=1,
            )
            if not profiles:
                _bump_throttle(request, identifier)
                messages.error(request, generic_error)
                return render(request, "login.html", {"form_username": identifier})

            profile = profiles[0]
            if not profile.get("is_active", True):
                _bump_throttle(request, identifier)
                messages.error(request, generic_error)
                return render(request, "login.html", {"form_username": identifier})

        except Exception as e:
            logger.error("Profile lookup: %s", type(e).__name__)
            messages.error(request, "Login failed. Please try again.")
            return render(request, "login.html", {"form_username": identifier})

        # Success
        _clear_throttle(request, identifier)

        shadow_user = _get_or_create_shadow_user(
            supabase_user_id=supabase_uid,
            username=profile.get("username", identifier),
            email=login_email,
            first_name=profile.get("first_name", ""),
            last_name=profile.get("last_name", ""),
        )

        role = profile.get("role", "student")
        if role == "admin" and not shadow_user.is_staff:
            shadow_user.is_staff = True
            shadow_user.save(update_fields=["is_staff"])
        elif role != "admin" and shadow_user.is_staff:
            shadow_user.is_staff = False
            shadow_user.save(update_fields=["is_staff"])

        # Store in session
        request.session.cycle_key()
        request.session["supabase_user_id"] = supabase_uid
        request.session["supabase_access_token"] = response.session.access_token
        request.session["supabase_refresh_token"] = response.session.refresh_token
        request.session["supabase_role"] = role
        request.session["django_user_id"] = shadow_user.pk

        from django.contrib.auth import login as django_login
        django_login(request, shadow_user)

        display_name = shadow_user.first_name or shadow_user.username
        messages.success(request, f"Welcome back, {display_name}!")

        if role == "admin":
            return redirect("admin_dashboard")
        return redirect("student_dashboard")

    return render(request, "login.html")


# ─── Logout View ─────────────────────────────────────────────────────────────

@require_POST
def supabase_logout_view(request):
    """Sign out: flush Django session."""
    request.session.flush()
    messages.info(request, "You have been logged out.")
    return redirect("student_login")
