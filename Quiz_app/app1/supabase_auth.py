"""
Supabase Auth views for PlacementIQ.
Handles student registration, login, logout, forgot password, and Google OAuth.
Templates, URLs, and form fields remain unchanged.
"""

import logging
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .models import SupabaseUserMapping
from . import supabase_client as sb

logger = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r'^[a-z0-9._]{3,30}$')


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


def _login_user_from_supabase(request, supabase_uid, session, profile, login_email):
    """Common login logic: create shadow user, set session, redirect."""
    shadow_user = _get_or_create_shadow_user(
        supabase_user_id=supabase_uid,
        username=profile.get("username", login_email.split("@")[0]),
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

    # Store in session (server-side only)
    request.session.cycle_key()
    request.session["supabase_user_id"] = supabase_uid
    request.session["supabase_access_token"] = session.access_token
    request.session["supabase_refresh_token"] = session.refresh_token
    request.session["supabase_role"] = role
    request.session["django_user_id"] = shadow_user.pk

    from django.contrib.auth import login as django_login
    django_login(request, shadow_user)

    display_name = shadow_user.first_name or shadow_user.username
    messages.success(request, f"Welcome back, {display_name}!")

    if role == "admin":
        return redirect("admin_dashboard")

    # Student login: attach any imported (pending) profile data by email and
    # ensure a StudentProfile exists. Never blocks login on failure.
    try:
        from .services import claim_pending_student
        claim_pending_student(shadow_user)
    except Exception:
        pass
    return redirect("student_dashboard")


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

            if response.user.identities is not None and len(response.user.identities) == 0:
                messages.error(request, "An account with this email already exists. Please sign in instead.")
                return render(request, "register.html", form_data)

            if not response.session:
                messages.success(
                    request,
                    "Account created! Please check your email to verify your address, then sign in."
                )
                return redirect("student_login")

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


# ─── Login View (NO rate limiting — unlimited retries allowed) ───────────────

@require_http_methods(["GET", "POST"])
def supabase_login_view(request):
    """Student login via Supabase Auth. Accepts username or email. No lockout."""
    if request.session.get("supabase_user_id"):
        return redirect("student_dashboard")

    if request.method == "POST":
        identifier = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        generic_error = "Invalid username/email or password."

        if not identifier or not password:
            messages.error(request, generic_error)
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
                messages.error(request, generic_error)
                return render(request, "login.html", {"form_username": identifier})

        # Authenticate with Supabase
        try:
            response = sb.sign_in_with_password(email=login_email, password=password)
            if not response.user or not response.session:
                messages.error(request, generic_error)
                return render(request, "login.html", {"form_username": identifier})
        except sb.SupabaseError as e:
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
                messages.error(request, generic_error)
                return render(request, "login.html", {"form_username": identifier})

            profile = profiles[0]
            if not profile.get("is_active", True):
                messages.error(request, generic_error)
                return render(request, "login.html", {"form_username": identifier})

        except Exception as e:
            logger.error("Profile lookup: %s", type(e).__name__)
            messages.error(request, "Login failed. Please try again.")
            return render(request, "login.html", {"form_username": identifier})

        # Success — log in
        return _login_user_from_supabase(request, supabase_uid, response.session, profile, login_email)

    return render(request, "login.html")


# ─── Forgot Password View ────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def supabase_forgot_password_view(request):
    """Send a password reset email via Supabase."""
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()

        if not email or "@" not in email:
            messages.error(request, "Please enter a valid email address.")
            return render(request, "forgot_password.html", {"form_email": email})

        # Build the redirect URL where user lands after clicking the reset link.
        # Use APP_BASE_URL if set (production), otherwise build from request.
        base = getattr(settings, "APP_BASE_URL", "") or ""
        if base:
            redirect_url = f"{base}/student/reset-password/"
        else:
            redirect_url = request.build_absolute_uri("/student/reset-password/")

        # Always show success message (prevents email enumeration)
        try:
            sb.reset_password_for_email(email, redirect_to=redirect_url)
        except Exception as e:
            logger.warning("Password reset request failed: %s", type(e).__name__)

        messages.success(
            request,
            "If an account with that email exists, you'll receive a password reset link shortly. Check your inbox."
        )
        return redirect("student_login")

    return render(request, "forgot_password.html")


# ─── Reset Password Confirm View ─────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def supabase_reset_password_view(request):
    """
    User lands here after clicking the reset link in their email.
    The access_token comes as a URL fragment (#) or query param.
    We use JavaScript to extract it from the fragment and pass it to the form.
    """
    if request.method == "POST":
        access_token = request.POST.get("access_token") or ""
        new_password = request.POST.get("password") or ""
        confirm_password = request.POST.get("confirm_password") or ""

        if not access_token:
            messages.error(request, "Invalid or expired reset link. Please request a new one.")
            return redirect("forgot_password")

        if not new_password:
            messages.error(request, "Password is required.")
            return render(request, "reset_password.html")

        if new_password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "reset_password.html")

        # Validate password strength
        try:
            validate_password(new_password)
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return render(request, "reset_password.html")

        # Update password via Supabase
        try:
            sb.update_user_password(access_token, new_password)
            messages.success(request, "Password updated successfully! Please sign in with your new password.")
            return redirect("student_login")
        except sb.SupabaseError as e:
            if "expired" in e.message.lower() or "invalid" in e.message.lower():
                messages.error(request, "This reset link has expired. Please request a new one.")
                return redirect("forgot_password")
            else:
                logger.error("Password update failed: %s", e.message[:200])
                messages.error(request, "Failed to update password. Please try again.")
                return render(request, "reset_password.html")
        except Exception as e:
            logger.error("Password update error: %s", type(e).__name__)
            messages.error(request, "Failed to update password. Please try again.")
            return render(request, "reset_password.html")

    return render(request, "reset_password.html")


# ─── Google OAuth (PKCE flow — reliable, server-readable ?code=) ─────────────

def _start_google_oauth(request):
    """Generate PKCE pair, store verifier in session, redirect to Supabase."""
    verifier, challenge = sb.generate_pkce_pair()
    request.session["oauth_code_verifier"] = verifier
    request.session["oauth_started"] = True

    base = getattr(settings, "APP_BASE_URL", "") or ""
    if base:
        callback_url = f"{base}/auth/google/callback/"
    else:
        callback_url = request.build_absolute_uri("/auth/google/callback/")
    oauth_url = sb.get_oauth_url("google", redirect_to=callback_url, code_challenge=challenge)
    return redirect(oauth_url)


def supabase_google_login_view(request):
    """Sign in with Google (PKCE). Same flow handles both login and signup."""
    if request.session.get("supabase_user_id"):
        return redirect("student_dashboard")
    try:
        return _start_google_oauth(request)
    except Exception as e:
        logger.error("Google OAuth initiation failed: %s", type(e).__name__)
        messages.error(request, "Unable to connect to Google. Please try again.")
        return redirect("student_login")


def supabase_google_signup_view(request):
    """Sign up with Google — identical PKCE flow (Supabase auto-creates the account)."""
    if request.session.get("supabase_user_id"):
        return redirect("student_dashboard")
    try:
        return _start_google_oauth(request)
    except Exception as e:
        logger.error("Google OAuth signup initiation failed: %s", type(e).__name__)
        messages.error(request, "Unable to connect to Google. Please try again.")
        return redirect("student_register")


@csrf_exempt
def supabase_google_callback_view(request):
    """
    Handle the callback from Supabase Google OAuth.

    Primary (PKCE): Supabase redirects here with ?code=... (query param).
    We exchange it together with the code_verifier stored in the session.

    Fallback (implicit): if tokens arrive as a URL hash fragment, a tiny JS
    page (google_callback.html) POSTs the access_token back to this endpoint.
    """
    # OAuth provider error (user denied, config issue, etc.)
    error_code = request.GET.get("error") or ""
    error_desc = request.GET.get("error_description") or ""
    if error_code or error_desc:
        readable = error_desc.replace("+", " ")
        logger.warning("Google OAuth error: code=%s desc=%s", error_code, readable[:300])

        # User explicitly cancelled — quiet message.
        if error_code in ("access_denied", "user_cancelled"):
            messages.error(request, "Google sign-in was cancelled.")
        else:
            # Surface the real reason so configuration issues are diagnosable.
            detail = readable or error_code or "unknown error"
            messages.error(request, f"Google sign-in failed: {detail}")
        return redirect("student_login")

    # ── Primary: PKCE authorization code in query string ──
    code = request.GET.get("code")
    if code:
        verifier = request.session.pop("oauth_code_verifier", None)
        try:
            response = sb.exchange_code_for_session(code, code_verifier=verifier)
            if not response.user or not response.session:
                messages.error(request, "Google sign-in failed. Please try again.")
                return redirect("student_login")
            return _complete_oauth_login(request, response.user, response.session)
        except Exception as e:
            logger.error("Google OAuth code exchange: %s: %s", type(e).__name__, str(e)[:200])
            messages.error(request, "Google sign-in failed. Please try again.")
            return redirect("student_login")

    # ── Fallback: access_token POSTed by the JS hash extractor ──
    if request.method == "POST":
        access_token = request.POST.get("access_token") or ""
        refresh_token = request.POST.get("refresh_token") or ""
        code_post = request.POST.get("code") or ""

        if code_post and not access_token:
            verifier = request.session.pop("oauth_code_verifier", None)
            try:
                response = sb.exchange_code_for_session(code_post, code_verifier=verifier)
                if response.user and response.session:
                    return _complete_oauth_login(request, response.user, response.session)
            except Exception as e:
                logger.error("Google OAuth code POST: %s", type(e).__name__)
            messages.error(request, "Google sign-in failed. Please try again.")
            return redirect("student_login")

        if not access_token:
            messages.error(request, "Google sign-in failed. Please try again.")
            return redirect("student_login")

        try:
            user = sb.get_user(access_token)
            if not user:
                messages.error(request, "Google sign-in failed. Please try again.")
                return redirect("student_login")
            session = sb.SupabaseSession(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=3600,
            )
            return _complete_oauth_login(request, user, session)
        except Exception as e:
            logger.error("Google OAuth token verify: %s", type(e).__name__)
            messages.error(request, "Google sign-in failed. Please try again.")
            return redirect("student_login")

    # ── Fallback: access_token in query string (rare implicit config) ──
    access_token = request.GET.get("access_token")
    if access_token:
        refresh_token = request.GET.get("refresh_token") or ""
        try:
            user = sb.get_user(access_token)
            if user:
                session = sb.SupabaseSession(
                    access_token=access_token, refresh_token=refresh_token, expires_in=3600,
                )
                return _complete_oauth_login(request, user, session)
        except Exception:
            pass
        messages.error(request, "Google sign-in failed. Please try again.")
        return redirect("student_login")

    # ── No code/token in query — render JS page to read the hash fragment ──
    return render(request, "google_callback.html")


def _derive_username_from_email(email):
    """Build a valid username (^[a-z0-9._]{3,30}$) from an email local part."""
    local = (email or "user").split("@")[0].lower()
    cleaned = re.sub(r"[^a-z0-9._]", "", local) or "user"
    cleaned = cleaned[:30]
    while len(cleaned) < 3:
        cleaned += "0"
    return cleaned


def _complete_oauth_login(request, user, session):
    """
    Complete OAuth login (Google sign-in / sign-up):
    - Ensure a Supabase profiles row exists (role always 'student' for OAuth).
    - Create/update the Django shadow user.
    - Set the session and redirect.
    """
    supabase_uid = user.id
    login_email = user.email or ""

    try:
        profiles = sb.table_select(
            "profiles",
            columns="is_active,role,username,first_name,last_name",
            filters={"id": supabase_uid},
            limit=1,
        )
    except Exception as e:
        logger.warning("OAuth profile lookup failed: %s", type(e).__name__)
        profiles = []

    if profiles:
        profile = profiles[0]
        if not profile.get("is_active", True):
            messages.error(request, "Your account has been deactivated. Please contact support.")
            return redirect("student_login")
    else:
        # New Google user — build a profile and persist it. Role is ALWAYS student;
        # Google signup can never create an admin.
        metadata = user.user_metadata or {}
        full_name = (metadata.get("full_name") or metadata.get("name") or "").strip()
        first_name = metadata.get("first_name") or metadata.get("given_name") or (full_name.split(" ")[0] if full_name else "")
        last_name = metadata.get("last_name") or metadata.get("family_name") or (" ".join(full_name.split(" ")[1:]) if full_name else "")

        # Choose a unique username
        base_username = metadata.get("username") or _derive_username_from_email(login_email)
        username = base_username
        try:
            existing = sb.table_select("profiles", columns="id", filters={"username": username}, limit=1)
            if existing:
                username = f"{base_username[:24]}{str(supabase_uid)[:5]}"
        except Exception:
            pass

        profile = {
            "username": username,
            "first_name": first_name or "",
            "last_name": last_name or "",
            "role": "student",
            "is_active": True,
        }

        # Persist the profile row (idempotent upsert on the user id).
        try:
            sb.table_upsert(
                "profiles",
                {
                    "id": supabase_uid,
                    "username": profile["username"],
                    "first_name": profile["first_name"],
                    "last_name": profile["last_name"],
                    "role": "student",
                    "is_active": True,
                },
                on_conflict="id",
            )
        except Exception as e:
            logger.warning("OAuth profile upsert failed (continuing): %s", type(e).__name__)

    return _login_user_from_supabase(request, supabase_uid, session, profile, login_email)


# ─── Logout View ─────────────────────────────────────────────────────────────

@require_POST
def supabase_logout_view(request):
    """Sign out: flush Django session."""
    request.session.flush()
    messages.info(request, "You have been logged out.")
    return redirect("student_login")
