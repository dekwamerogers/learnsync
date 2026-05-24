# LearnSync

> Internal learner-progress tracking platform for Learner Community Executives (LCEs) and Programme Managers.

LearnSync replaces manual spreadsheets with a searchable, filterable web app that ingests staff portal CSV exports, computes health flags per learner, and generates management reports — all without touching the LMS or staff portal directly.

---

## What it does

- **Ingests** learner progress data from staff portal CSV exports
- **Scores** each enrolment with health flags (Inactive, Never Activated, Stuck on Assignment, Low Pass Rate, Stalled Between Courses, Payment Issue)
- **Tracks** every LCE intervention — calls, emails, eHub messages, meetings
- **Reports** programme health in a manager-facing view with Excel (9 sheets) and PDF (with charts) downloads
- **Monitors** pod groups — cohorts of learners committed to a target graduation date — and computes pace status per learner

## What it does NOT do

- Host or manage learning content (that is the LMS / eHub)
- Process payments
- Replace the staff portal

---

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.x |
| Framework | Django 6.x |
| Database | SQLite (local dev) / MySQL (production) |
| Frontend | Server-rendered Django templates + Bootstrap 5 |
| Charts | Chart.js 4 |
| Partial updates | HTMX (filter bar swaps on Analytics page) |
| Forms | django-crispy-forms + crispy-bootstrap5 |
| Tables | django-tables2 + django-filter |
| Excel export | openpyxl |
| PDF export | ReportLab (Platypus layout + Graphics charts) |
| Background jobs | `threading.Thread(daemon=True)` — in-process, no external queue |
| Rate limiting | django-axes (account/IP lockout after 5 failed login attempts) |
| Static files | WhiteNoise (compressed + hashed filenames) |

---

## Project structure

```
LDP/
├── lce_platform/
│   ├── lce_platform/          # Django project config (settings, urls, wsgi)
│   │   ├── settings.py
│   │   ├── urls.py
│   │   └── wsgi.py
│   ├── core/                  # Shared models (Programme, Cohort, Learner, Enrollment)
│   ├── ingestion/             # CSV ingestion models, context processor
│   ├── dashboard/             # Dashboard models
│   ├── selfpaced/             # Main app — all views, health engine, exports
│   │   ├── health.py          # Health flag computation (pure functions)
│   │   ├── engine.py          # Ingestion processing engine
│   │   ├── parsing.py         # CSV parsing and validation
│   │   ├── detector.py        # Programme/course detection from eHub class names
│   │   ├── pace.py            # Pod pace calculation
│   │   ├── tasks.py           # Background job runners (daemon threads)
│   │   ├── views/             # One module per feature area
│   │   ├── filters.py         # django-filter FilterSets
│   │   ├── forms.py           # Django forms
│   │   ├── exports.py         # Excel / CSV export helpers
│   │   └── templatetags/      # Custom template filters
│   ├── templates/             # All HTML templates
│   │   ├── selfpaced/         # App templates
│   │   ├── 404.html           # Standalone error pages
│   │   ├── 500.html
│   │   ├── 403.html
│   │   └── 400.html
│   ├── static/                # Source static files
│   ├── staticfiles/           # collectstatic output (not committed)
│   ├── passenger_wsgi.py      # cPanel Passenger entry point
│   └── requirements.txt
├── DEPLOY_CHECKLIST.md        # Step-by-step cPanel deployment guide
├── HANDOFF.md                 # Full technical handoff — URL map, metrics, data model
└── .venv/                     # Local virtual environment (not committed)
```

---

## Local development

### 1. Clone and create a virtual environment

```bash
git clone <repo-url>
cd LDP
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r lce_platform/requirements.txt
```

> `mysqlclient` is listed in `requirements.txt` for the production server. If it fails to compile locally, comment it out — it is only needed when `DB_ENGINE=django.db.backends.mysql`.

### 3. Configure environment

```bash
cp lce_platform/.env.example lce_platform/.env
# Edit .env — the defaults work for local SQLite dev with no changes needed
```

### 4. Run migrations and create a superuser

```bash
cd lce_platform
python manage.py migrate
python manage.py createsuperuser
```

### 5. Start the dev server

```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000/`. Log in with the superuser you just created.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DEBUG` | `False` | Set `True` for local dev |
| `SECRET_KEY` | insecure dev key | **Required in production** — generate with `python -c "import secrets; print(secrets.token_hex(50))"` |
| `ALLOWED_HOSTS` | `localhost` | Comma-separated hostnames |
| `CSRF_TRUSTED_ORIGINS` | _(empty)_ | Required behind a reverse proxy, e.g. `https://yourdomain.com` |
| `DB_ENGINE` | `django.db.backends.sqlite3` | Use `django.db.backends.mysql` in production |
| `DB_NAME` | `learnsync` | Database name |
| `DB_USER` | `learnsync` | Database user |
| `DB_PASSWORD` | _(empty)_ | Database password |
| `DB_HOST` | `localhost` | Database host |
| `DB_PORT` | `3306` | Database port |
| `DJANGO_LOG_LEVEL` | `INFO` | Django logger level |

---

## Key features

### Health scoring

Every enrolment is scored on each CSV upload. Six flags are computed in priority order:

| Flag | Trigger |
|---|---|
| Never Activated | Has first-sign-of-life but no assignment accessed within activation threshold (default 3 days) |
| Inactive | Had prior activity, no assignment accessed/submitted within inactivity threshold (default 7 days) |
| Stuck on Assignment | Accessed an assignment but hasn't submitted within stuck threshold (default 5 days) |
| Low Pass Rate | Pass rate on submitted assignments below threshold (default 70%) |
| Stalled Between Courses | Completed a course but hasn't started the next within inter-course threshold (default 5 days) |
| Payment Issue | Payment status is anything other than Compliant |

Health status is rolled up from flags: **Graduated → Dormant → At Risk → Active → Not Started**.

All thresholds are configurable per programme via the admin.

### CSV ingestion

Upload flow (asynchronous — progress bar in UI):

1. File validated synchronously — returns error immediately if columns are missing
2. `IngestionJob` created and background daemon thread started
3. Engine maps each row to a Programme + Course via the eHub pattern registry
4. Unmatched rows go to a **flagged row queue** for admin review
5. Learner records upserted, assignment progress updated, health flags recomputed, snapshots created
6. Stalled jobs (`status='processing'` on startup) are auto-reset to `failed` by `AppConfig.ready()`

Three CSV types are supported:
- **Staff portal CSV** — full learner progress export (`/admin/upload/`)
- **Enrolment CSV** — simplified bulk enrolment (name, email, programme, date) (`/admin/enrolment-csv/upload/`)
- **Pod assignment CSV** — pod selection data from Google Form export (`/admin/pod-import/upload/`)

### Manager report downloads

`/reports/manager/` generates a comprehensive report in three formats:

- **HTML** — default view
- **Excel** (`?format=excel`) — 9 sheets: Summary, By Programme, Learner Detail, Payment Status, At-Risk Flags, Recent Interventions, Metric Definitions, Weekly Breakdown, Weekly by Programme
- **PDF** (`?format=pdf`) — landscape A4 with embedded bar, donut, and line charts

---

## URL overview

| Section | Prefix | Key pages |
|---|---|---|
| Dashboard | `/` | Home (daily briefing) |
| Learners | `/learners/` | List, profile |
| Programmes | `/programmes/` | List, detail |
| Analytics | `/analytics/` | Charts + cohort table (HTMX-filtered) |
| Interventions | `/interventions/` | Log, bulk log |
| Pods | `/pods/` | List, detail, assign, pace recompute |
| Portfolio | `/portfolio/` | Cross-programme executive summary |
| Reports | `/reports/manager/` | HTML / Excel / PDF |
| Admin | `/admin/` | Upload, ingestion log, programme CRUD, pattern registry, countries |

See [HANDOFF.md](HANDOFF.md) for the complete URL map with view names.

---

## Deployment

This app is deployed to a **cPanel shared host** using **Passenger WSGI** + **MySQL**.

See **[DEPLOY_CHECKLIST.md](DEPLOY_CHECKLIST.md)** for the full step-by-step guide covering:

- cPanel database setup (MySQL)
- Python app configuration
- Virtual environment and dependency installation
- `.env` file configuration
- Migrations and `collectstatic`
- SSL and security headers
- Smoke tests
- Troubleshooting (mysqlclient compile errors, CSRF issues, 500 on first load)

---

## Security

- All 60+ routes require authentication (`@login_required`)
- All POST forms include `{% csrf_token %}`; HTMX requests inject the CSRF token via `htmx:configRequest`
- Login is rate-limited via **django-axes** — accounts are locked for 1 hour after 5 consecutive failed attempts
- Upload endpoints block concurrent jobs (one active job at a time per upload type)
- Production enforces HTTPS, HSTS (1 year), secure cookies, `X-Frame-Options: DENY`, `X-Content-Type-Options`
- `SECRET_KEY` is rejected at startup if it is left as the insecure dev default in production

---

## Contributing

This is an internal tool. Access is restricted to staff with a Django account created by an administrator (`/admin/` → Auth → Users).

To create a staff account:

```bash
python manage.py createsuperuser
```

Or via Django admin at `/django-admin/`.

---

## License

Internal use only. Not licensed for redistribution.
