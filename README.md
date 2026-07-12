# PlacementIQ

AI-powered campus placement preparation platform built with Django + Supabase.
Students practice company-specific tests; staff manage companies, drives, quizzes,
students and results through role-based portals.

> Quick note (Hinglish): Ye README batati hai — site local pe kaise **run** karni hai,
> kya **edit** karna hai kahan se, aur website **kaise kaam** karti hai. Neeche sab
> steps diye hain.

---

## 1. Tech stack

- **Backend:** Django 5.2 (Python 3.12)
- **Database:** Supabase PostgreSQL (production data). Local tests use SQLite.
- **Auth:**
  - Students → Supabase Auth (email/password + Google OAuth)
  - Staff (Super Admin / Teacher) → Django authentication
- **Frontend:** Django templates + Bootstrap 5.3 + Bootstrap Icons (no build step)
- **AI questions:** OpenAI (optional; falls back to a built-in question bank)

---

## 2. Folder layout (where things live)

```
QuizApp/                         <- repository root
├─ myenv/                        <- Python virtual environment
├─ README.md                     <- this file
└─ Quiz_app/                     <- Django project root (run commands from HERE)
   ├─ manage.py                  <- Django entry point
   ├─ .env                       <- environment variables (secrets, DB URL, DEBUG)
   ├─ db.sqlite3                 <- local SQLite (only used if no DATABASE_URL)
   ├─ templates/                 <- ALL HTML pages
   │  ├─ base.html               <- global layout + navbar + footer
   │  ├─ admin_*.html            <- Super Admin portal pages
   │  ├─ teacher/                <- Teacher portal pages (base_teacher.html = layout)
   │  └─ admin/                  <- Super Admin teacher-management + audit logs
   ├─ static/
   │  ├─ css/style.css           <- ALL site styling (colors, cards, layout)
   │  └─ js/                     <- theme toggle + small scripts
   ├─ media/                     <- uploaded files (company logos, etc.)
   └─ Quiz_app/                  <- settings package
      ├─ settings.py             <- configuration (DB, security, apps)
      └─ urls.py                 <- URL routes (which URL -> which view)
   └─ app1/                      <- the main application (all Python logic)
      ├─ models.py               <- database tables (Company, Quiz, Attempt, ...)
      ├─ views.py                <- student + Super Admin views
      ├─ teacher_views.py        <- Teacher portal views
      ├─ admin_teacher_views.py  <- Super Admin: manage teachers + audit logs
      ├─ forms.py                <- input forms (whitelisted, safe)
      ├─ permissions.py          <- role helpers (is_super_admin/is_teacher/...)
      ├─ decorators.py           <- access guards (@superadmin_required, ...)
      ├─ services.py             <- audit logging, CSV safety, pending students
      ├─ supabase_auth.py        <- student login/register/Google/reset
      ├─ analytics.py            <- analytics queries
      ├─ ai_service.py           <- AI question generation
      ├─ context_processors.py   <- role flags available in every template
      ├─ migrations/             <- database schema history
      └─ management/commands/    <- custom commands (setup_roles, set_superadmin)
```

**Rule of thumb:**
- Change how a page **looks** → edit files in `templates/` and `static/css/style.css`.
- Change what a page **does** (logic, data) → edit the matching view in `app1/`.
- Change the **database shape** → edit `app1/models.py`, then make + run migrations.
- Change a **URL** → edit `Quiz_app/urls.py`.

---

## 3. Run the site locally

> **Important:** Run every command from the project root
> `C:\Users\amles\OneDrive\Desktop\django\QuizApp\Quiz_app`
> (the folder that contains `manage.py`). Do **not** `cd` into the inner
> `Quiz_app\Quiz_app` folder.

### One-time setup
```powershell
# from QuizApp\Quiz_app
..\myenv\Scripts\python.exe -m pip install -r ..\requirements.txt
..\myenv\Scripts\python.exe manage.py migrate
..\myenv\Scripts\python.exe manage.py setup_roles
```

### Start the server
```powershell
# from QuizApp\Quiz_app
..\myenv\Scripts\python.exe manage.py runserver 127.0.0.1:3000
```
Then open: **http://localhost:3000/**

Stop the server with **Ctrl + C**.

> Paste each command on its own line, not as one block.

---

## 4. Environment variables (`.env`)

The file `Quiz_app/.env` controls configuration. Key values:

| Variable            | Meaning                                                        |
|---------------------|----------------------------------------------------------------|
| `DJANGO_DEBUG`      | `true` for local development, `false` only in production.      |
| `DATABASE_URL`      | Supabase PostgreSQL connection string (do not share/commit).   |
| `APP_BASE_URL`      | Base URL used for auth callbacks (`http://localhost:3000`).    |
| `ALLOWED_HOSTS`     | Hosts allowed to serve the app.                                |
| `DJANGO_SECRET_KEY` | Django secret (required when `DEBUG=false`).                   |
| `SUPABASE_*`        | Supabase keys for student auth (keep secret).                  |

> **For local testing keep `DJANGO_DEBUG=true`.** When `false`, Django forces
> HTTPS redirects and secure cookies, which the local dev server cannot serve —
> that causes the "failed to load" / redirect-to-https problem.

---

## 5. User roles (how the website works)

Three roles, enforced on the backend (not just hidden in the UI):

| Role         | Django flags                          | Logs in at        | Lands on            |
|--------------|---------------------------------------|-------------------|---------------------|
| Super Admin  | `is_superuser=True`, `is_staff=True`  | `/admin/login/`   | `/admin/dashboard/` |
| Teacher      | `is_staff=True`, in "Teacher" group   | `/admin/login/`   | `/teacher/dashboard/`|
| Student      | `is_staff=False` (Supabase Auth)      | `/student/login/` | `/student/dashboard/`|

**Super Admin** — full control: manage teachers, students, companies, drives,
quizzes, results, analytics, settings, audit logs.

**Teacher** — dedicated `/teacher/` panel:
- Companies (view all; create/edit own or assigned; cannot delete)
- Placement drives (create/edit own or assigned)
- Quizzes + questions (create/edit/delete own; CSV upload; AI generate)
- Students (view, safe-field edit only; cannot delete or change roles)
- Results (view, export; import offline results)
- Analytics (scoped to managed quizzes)
- Import/Export with an import history + audit log

**Student** — existing experience is unchanged: browse companies, attempt active
tests, see results and analytics. Google/email login works via Supabase.

### Key safety rules built in
- Teachers can only edit their **own / assigned** companies, drives, quizzes
  (checked on every URL — no editing by guessing IDs).
- Deleting a quiz that has results **archives** it instead (results preserved).
- Student edit form can never set `is_staff`, `is_superuser`, groups, password
  or Supabase IDs.
- CSV exports escape `= + - @` to prevent spreadsheet formula injection.
- Every sensitive action is written to the **Audit Log**.

---

## 6. Log in / test accounts

### Super Admin
Current username: `admin`. To change the username/password (secure, hidden prompt):
```powershell
..\myenv\Scripts\python.exe manage.py set_superadmin
```

### Test Teacher (created for you)
- URL: `http://localhost:3000/admin/login/`
- Username: `teacher1`
- Password: `Teacher@12345`

> This is a test account. Delete or deactivate it before real deployment
> (Super Admin → Teachers → toggle/deactivate).

### Create more teachers
Log in as Super Admin → **Teachers → Add Teacher**.

---

## 7. Editing common things (how-to)

**Change colors / card styles / fonts**
→ `Quiz_app/static/css/style.css` (CSS variables like `--primary`, `--muted`).

**Edit a page's text/layout**
→ find the template in `Quiz_app/templates/`. Teacher pages extend
`templates/teacher/base_teacher.html`; everything extends `templates/base.html`.

**Change the sidebar/menu**
→ Teacher menu: `templates/teacher/base_teacher.html`.
→ Top navbar: `templates/base.html`.

**Add / change a field on a model**
1. Edit `app1/models.py`
2. `..\myenv\Scripts\python.exe manage.py makemigrations`
3. `..\myenv\Scripts\python.exe manage.py migrate`

**Add a new page**
1. Add a view function in the right `*_views.py`
2. Add a route in `Quiz_app/urls.py`
3. Add a template in `templates/`

**Add a permission/role rule**
→ `app1/permissions.py` (helpers) and `app1/decorators.py` (guards).

---

## 8. Running the tests

```powershell
# from QuizApp\Quiz_app
..\myenv\Scripts\python.exe manage.py check
..\myenv\Scripts\python.exe manage.py test app1
```
Tests run on a fast in-memory SQLite database (they never touch Supabase).
Expected result: all tests pass (`OK`).

---

## 9. Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `'..\myenv\Scripts\python.exe' is not recognized` | You are in the wrong folder. Be in `QuizApp\Quiz_app` (has `manage.py`). If you see `...\Quiz_app\Quiz_app>`, run `cd ..`. |
| Browser "failed to load" / keeps redirecting to `https://` | `DJANGO_DEBUG` is `false`. Set `DJANGO_DEBUG=true` in `.env` and restart the server. |
| `Invalid HTTP_HOST header` | Add the host to `ALLOWED_HOSTS` in `.env`. |
| Port already in use | Run on another port, e.g. `runserver 127.0.0.1:8000`. |
| Static files/CSS missing after `DEBUG=false` | Run `manage.py collectstatic` (only needed for production). |
| Changed `.env` but nothing changed | Restart the server (it reads `.env` at startup). |

---

## 10. Deployment (later)

Deployment is done **after** local testing. In short, on the server:
```bash
cd /var/www/placementiq && git pull
source venv/bin/activate && cd Quiz_app
python manage.py migrate
python manage.py setup_roles
python manage.py collectstatic --noinput
python manage.py check
sudo systemctl restart placementiq && sudo systemctl reload nginx
```
Set `DJANGO_DEBUG=false` and a real `DJANGO_SECRET_KEY` in the server's `.env`.
Also apply `Quiz_app/supabase_security_hardening_teacher.sql` in Supabase to lock
down the new tables (enables RLS + revokes public API access).

---

## 11. Security reminders

- Never commit `.env` or share Supabase keys / DB passwords.
- Rotate any keys that were previously exposed.
- Keep `DJANGO_DEBUG=false` in production only.
- The `supabase_security_hardening*.sql` files lock Django tables away from the
  public Supabase Data API — apply them after deploying schema changes.
