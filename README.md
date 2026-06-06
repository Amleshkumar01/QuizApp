# Quiz App — Secure & Modern Django Quiz Platform

**English** | [हिंदी में पढ़ें](#हिंदी-में-प्रोजेक्ट-कैसे-चलाएं)

A Django 5 web application for timed multiple-choice quizzes. Users register, browse categories, take quizzes, and view scores. Staff manage users and quizzes; questions/options are added via Django admin.

---

## Features

| Area | What you get |
|------|----------------|
| **Users** | Register, login, logout, password validation |
| **Quizzes** | Shuffled questions, **server-side timer** (60s/question), score & history |
| **Security** | CSRF, login throttling, registration limits, session hardening, staff-only admin |
| **UI** | Bootstrap 5, Inter font, responsive cards, progress bar, score ring |
| **Admin** | Custom dashboard + Django `/admin/` for questions & options |

---

## Quick start (English)

### 1. Go to the Django project folder

```powershell
cd C:\Users\amles\OneDrive\Desktop\django\QuizApp\Quiz_app
```

### 2. Virtual environment (recommended)

**Windows PowerShell:**

```powershell
python -m venv myenv
.\myenv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
python3 -m venv myenv
source myenv/bin/activate
```

### 3. Install packages

```bash
pip install -r ..\requirements.txt
```

Or:

```bash
pip install django pillow
```

### 4. Database setup

```bash
python manage.py migrate
```

### 5. Create admin user

```bash
python manage.py createsuperuser
```

Mark the user as **Staff** in Django admin if they should use `/admin/dashboard/`.

### 6. Run the server

```bash
python manage.py runserver
```

Open: **http://127.0.0.1:8000/**

Stop with `Ctrl+C`.

---

## Adding quiz content

1. Run the server and open **http://127.0.0.1:8000/admin/**
2. Log in as superuser
3. Create a **Category** (optional image)
4. Create a **Quiz** (link to category, set status **active**)
5. Add **Questions** and **Options** (mark one option as correct per question)
6. On the site home page, open the category and **Start Quiz**

Custom staff pages (`/admin/quizzes/`) manage quiz metadata only; questions are in Django admin.

---

## Main URLs

| URL | Description |
|-----|-------------|
| `/` | Home — categories |
| `/register/` | Sign up |
| `/login/` | Sign in |
| `/category/<id>/` | Quizzes in category |
| `/quiz/<id>/start/` | Begin attempt (login required) |
| `/my-attempts/` | Your history |
| `/admin/` | Django admin (questions, options) |
| `/admin/dashboard/` | Staff dashboard |

---

## Environment variables (production)

Copy `.env.example` to `.env` and set:

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Required when `DJANGO_DEBUG=false` |
| `DJANGO_DEBUG` | `false` in production |
| `DJANGO_ALLOWED_HOSTS` | e.g. `example.com,www.example.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | e.g. `https://example.com` |
| `QUIZ_QUESTION_SECONDS` | Server timer per question (default `60`) |

---

## Security overview

- **CSRF** on all POST forms
- **Login throttle** — 5 failures per username / 25 per IP per 15 min
- **Registration throttle** — 10 sign-ups per IP per hour
- **Quiz timer** — deadline stored in session; expired answers are skipped server-side
- **Submit lock** — prevents duplicate attempts from double-click
- **Staff routes** — `staff_member_required`; superusers protected from staff delete/edit
- **Production** — secure cookies, HSTS, SSL redirect when `DEBUG` is off

For multiple server processes, use **Redis** for `CACHES` instead of LocMem.

---

## Project structure

```text
QuizApp/
├── README.md
├── requirements.txt
├── .env.example
└── Quiz_app/              ← run commands here
    ├── manage.py
    ├── db.sqlite3
    ├── Quiz_app/          ← settings, urls
    ├── app1/              ← models, views
    ├── templates/
    └── static/
        ├── css/style.css
        └── js/quiz.js
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No module named 'django'` | Activate venv, `pip install -r requirements.txt` |
| DB errors | `python manage.py migrate` |
| Port in use | `python manage.py runserver 8001` |
| Static CSS missing in prod | `python manage.py collectstatic` |

---

## हिंदी में — प्रोजेक्ट कैसे चलाएं

### यह ऐप क्या करता है?

यह एक **Quiz Application** है। User register/login करके category चुनता है, quiz attempt करता है, score देखता है। Admin users और quizzes manage कर सकता है। Questions Django admin (`/admin/`) से add होते हैं।

### चलाने के स्टेप्स

**1.** Terminal खोलें और project folder में जाएं:

```powershell
cd C:\Users\amles\OneDrive\Desktop\django\QuizApp\Quiz_app
```

**2.** Virtual environment बनाएं और activate करें:

```powershell
python -m venv myenv
.\myenv\Scripts\Activate.ps1
```

**3.** Packages install करें:

```bash
pip install -r ..\requirements.txt
```

**4.** Database तैयार करें:

```bash
python manage.py migrate
```

**5.** Admin user बनाएं:

```bash
python manage.py createsuperuser
```

**6.** Server start करें:

```bash
python manage.py runserver
```

Browser में खोलें: **http://127.0.0.1:8000/**

### Quiz कैसे add करें?

1. `http://127.0.0.1:8000/admin/` पर login करें  
2. **Category** बनाएं  
3. **Quiz** बनाएं (status = **active**)  
4. **Question** और **Option** add करें (एक सही answer mark करें)  
5. Home page से category खोलकर **Start Quiz** दबाएं  

### सुरक्षा (Security)

- Login पर गलत password की कोशिश limit होती है  
- Registration spam से बचाव (IP limit)  
- Quiz का timer server पर भी check होता है  
- Staff के अलावा admin pages नहीं खुलते  

### समस्या आए तो

- Django नहीं मिला → venv activate करके `pip install` फिर से करें  
- Database error → `python manage.py migrate` चलाएं  
- Port busy → `python manage.py runserver 8001` use करें  

---

## Tech stack

- **Backend:** Django 5.2, SQLite  
- **Frontend:** Bootstrap 5.3, Bootstrap Icons, custom CSS  
- **Auth:** Django built-in `User` model  

---

*Quiz App — learn, attempt, improve.*
