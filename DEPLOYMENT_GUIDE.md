# PlacementIQ — Deployment Guide

Ye file batati hai ki GitHub pe push karne se **pehle** kya changes karne hain,
deployment ke time kya commands chalani hain, aur kaise ensure karein ki koi error
na aaye.

---

## Step 1: Push se PEHLE — Local pe ye changes karo

### 1.1 `.env` file ko PUSH MAT KARO

`.env` me secrets hain (DB password, Supabase keys). Ye file `.gitignore` me already
hai — isliye ye push nahi hogi. Lekin confirm karo:

```bash
git check-ignore Quiz_app/.env
# Output: Quiz_app/.env  ← ye aaye toh safe hai
```

### 1.2 `.env` me DJANGO_DEBUG change karo

**Local (development):**
```
DJANGO_DEBUG=true
```

**Production server pe `.env`:**
```
DJANGO_DEBUG=false
```

> KABHI production pe `DJANGO_DEBUG=true` mat rakho — secrets leak ho sakte hain.

### 1.3 `.env` me APP_BASE_URL change karo

**Local:**
```
APP_BASE_URL=http://localhost:8080
```

**Production:**
```
APP_BASE_URL=https://your-domain.com
```
Ya agar IP pe chal raha hai:
```
APP_BASE_URL=http://4.224.120.24
```

### 1.4 ALLOWED_HOSTS update karo

Production me apna domain/IP add karo:
```
ALLOWED_HOSTS=your-domain.com,4.224.120.24,127.0.0.1,localhost
```

### 1.5 DJANGO_SECRET_KEY set karo

Production me ek long random string rakhna (command se generate karo):
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```
Output ko `.env` me paste karo:
```
DJANGO_SECRET_KEY=your-50-char-random-string-here
```

### 1.6 Supabase Redirect URLs update karo

Supabase Dashboard → Authentication → URL Configuration → Redirect URLs me add karo:
```
https://your-domain.com/auth/google/callback/
https://your-domain.com/student/reset-password/
```
(Replace `your-domain.com` with your actual domain/IP)

### 1.7 Google OAuth Redirect URI update karo

Google Cloud Console → OAuth Client → Authorized redirect URIs:
```
https://your-supabase-url.supabase.co/auth/v1/callback
```

---

## Step 2: Super Admin Username / Password change karo

### Option A: Command line se (recommended — safe, hidden)
```bash
cd Quiz_app
python manage.py set_superadmin
```
Ye interactive hai — naya username aur password prompt karega (hidden input).

### Option B: Django shell se
```bash
cd Quiz_app
python manage.py shell
```
```python
from django.contrib.auth.models import User
u = User.objects.get(username='admin')  # current username
u.username = 'your_new_username'
u.set_password('YourNewStr0ngPass!')
u.save()
exit()
```

### Option C: Production server pe SSH ke baad
```bash
cd /var/www/placementiq/Quiz_app
source ../venv/bin/activate
python manage.py set_superadmin
```

> Password Django validators se validate hota hai (min 8 chars, not common, not similar to username).

---

## Step 3: GitHub pe Push karo

```bash
cd C:\Users\amles\OneDrive\Desktop\django\QuizApp

# Current branch check karo
git branch
# * feature/teacher-management  ← ye dikhe

# Sab changes staged hain? Check karo
git status

# Agar kuch unstaged hai toh add karo (specific files)
git add Quiz_app/ README.md DEPLOYMENT_GUIDE.md requirements.txt

# DO NOT add: Quiz_app/.env (gitignored hai already)

# Commit karo (agar uncommitted changes hain)
git commit -m "Ready for deployment"

# Main me merge karo
git checkout main
git merge feature/teacher-management

# Push karo
git push origin main
```

---

## Step 4: Production Server pe Deploy karo

### SSH into server:
```bash
ssh user@your-server-ip
```

### Pull latest code:
```bash
cd /var/www/placementiq
git pull origin main
```

### Activate venv:
```bash
source venv/bin/activate
cd Quiz_app
```

### Install new dependencies (if any):
```bash
pip install -r ../requirements.txt
```

### Run migrations:
```bash
python manage.py migrate
```

### Setup roles (Teacher group + permissions):
```bash
python manage.py setup_roles
```

### Collect static files:
```bash
python manage.py collectstatic --noinput
```

### Run system check:
```bash
python manage.py check --deploy
```
> `--deploy` flag extra security checks bhi run karta hai.

### Change Super Admin password (if needed):
```bash
python manage.py set_superadmin
```

### Restart services:
```bash
sudo systemctl restart placementiq
sudo systemctl reload nginx
sudo systemctl status placementiq --no-pager
```

---

## Step 5: Supabase Security (new tables ka RLS enable karo)

Supabase Dashboard → SQL Editor me ye file run karo:
```
Quiz_app/supabase_security_hardening_teacher.sql
```

Ye 10 new tables pe RLS enable karega aur anon/authenticated access revoke karega.
Django (postgres owner) pe koi effect nahi padega.

---

## Step 6: Deployment ke BAAD verify karo

Production pe ye sab check karo:

| Test | URL / Command | Expected |
|------|---------------|----------|
| Home page | `https://your-domain.com/` | 200, site loads |
| Student login | `https://your-domain.com/student/login/` | Login page shows |
| Google login | Click "Continue with Google" | Redirects to Google |
| Admin login | `https://your-domain.com/admin/login/` | Admin login page |
| Teacher login | `https://your-domain.com/teacher/login/` | Teacher login page |
| Teacher dashboard | Login as teacher → `/teacher/dashboard/` | Dashboard loads |
| Admin dashboard | Login as superadmin → `/admin/dashboard/` | Dashboard loads |
| Student profile | Login as student → click name → profile | Profile page |
| Create quiz | Teacher → My Quizzes → New Quiz | Works |
| Attempt quiz | Student → company → start quiz | Works |
| Static files | CSS loads, dark mode works | No broken styling |
| Django check | `python manage.py check --deploy` | No issues |

---

## Common Deployment Errors aur Solutions

| Error | Cause | Fix |
|-------|-------|-----|
| `DisallowedHost` | Domain not in ALLOWED_HOSTS | Add domain to `.env` ALLOWED_HOSTS |
| `CSRF verification failed` | Domain not in CSRF_TRUSTED_ORIGINS | Add `https://your-domain.com` to CSRF_TRUSTED_ORIGINS in `.env` |
| `301 redirect loop` | SECURE_SSL_REDIRECT on but no HTTPS | Setup Nginx SSL OR set SECURE_SSL_REDIRECT=False temporarily |
| `Static files 404` | Didn't run collectstatic | `python manage.py collectstatic --noinput` |
| `OperationalError: getaddrinfo failed` | DATABASE_URL wrong or DB unreachable | Check DATABASE_URL in `.env` |
| `No module found` | Dependencies not installed | `pip install -r requirements.txt` |
| `Permission denied` | File permissions | `sudo chown -R www-data:www-data /var/www/placementiq` |
| `502 Bad Gateway` | Gunicorn not running | `sudo systemctl restart placementiq` |

---

## Production `.env` Template

Create `/var/www/placementiq/Quiz_app/.env` with:

```env
# === SECURITY ===
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<generate-50-char-random-key>

# === HOSTS ===
ALLOWED_HOSTS=your-domain.com,4.224.120.24,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://your-domain.com,http://4.224.120.24

# === DATABASE ===
DATABASE_URL=postgresql://postgres.[your-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres

# === APP ===
APP_BASE_URL=https://your-domain.com

# === SUPABASE AUTH ===
SUPABASE_URL=https://[your-ref].supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...

# === OPTIONAL ===
OPENAI_API_KEY=sk-...
```

> KABHI ye file ko git me commit mat karo. Sirf server pe manually create karo.

---

## File Structure (kya push hoga, kya nahi)

```
PUSH HOGA (git tracked):
├── Quiz_app/
│   ├── manage.py
│   ├── Quiz_app/settings.py, urls.py
│   ├── app1/ (sab Python code)
│   ├── templates/ (sab HTML)
│   ├── static/ (CSS, JS)
│   └── supabase_security_hardening_teacher.sql
├── README.md
├── DEPLOYMENT_GUIDE.md
├── requirements.txt
└── .gitignore

PUSH NAHI HOGA (gitignored / manual):
├── Quiz_app/.env          ← secrets (manually create on server)
├── Quiz_app/db.sqlite3    ← local dev DB
├── Quiz_app/media/        ← uploaded files (manually backup)
├── myenv/                 ← virtual environment
└── __pycache__/           ← compiled Python
```

---

## Quick Reference Commands

```bash
# Super Admin password change
python manage.py set_superadmin

# Create a teacher (interactive)
# Login as Super Admin → /admin/teachers/add/

# Run tests
python manage.py test app1

# Check for issues
python manage.py check --deploy

# Generate secret key
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# Reset migrations (DANGER - only if needed)
# python manage.py migrate app1 zero
# python manage.py migrate app1
```

---

## Rollback (agar kuch galat ho jaaye)

```bash
# Code revert
git checkout main~1  # previous commit
git push -f origin main  # force push old version

# Database revert (only if migration broke something)
python manage.py migrate app1 0006  # go back to before teacher system

# Restart
sudo systemctl restart placementiq
```

---

Last updated: July 2026
