# LearnSync — cPanel Deployment Checklist (MySQL)

**Target:** cPanel shared hosting · MySQL database · Python/Passenger WSGI  
**Last updated:** May 2026

Tick each box as you go. Items are in dependency order — don't skip ahead.

---

## Phase 1 — Local Prep (do this on your machine before uploading)

### 1.1 — Code & Files
- [ ] Confirm all local changes are saved and the app runs cleanly with `python manage.py check`
- [ ] Run `python manage.py collectstatic --no-input` — this fills `staticfiles/` which gets uploaded
- [ ] Delete `db.sqlite3` from the upload bundle (the server will use MySQL)
- [ ] Delete `.venv/` from the upload bundle (you'll rebuild the venv on the server)
- [ ] Add a `.gitignore`-style exclude for `.env` — never upload the local `.env` to the server

### 1.2 — What to upload
Upload the entire `lce_platform/` folder contents. The server should receive:

```
manage.py
passenger_wsgi.py          ← new — cPanel entry point
requirements.txt           ← new — production dependencies
lce_platform/              ← Django project package (settings, urls, wsgi)
selfpaced/                 ← main app
core/ ingestion/ dashboard/← supporting apps
templates/
staticfiles/               ← pre-built static files (after collectstatic)
.env.production            ← rename to .env on the server
```

Do NOT upload: `.venv/`, `db.sqlite3`, `.env` (local dev), `__pycache__/`

---

## Phase 2 — cPanel Database Setup

### 2.1 — Create the MySQL database
- [ ] Log in to cPanel → **MySQL Databases**
- [ ] Create a new database (name will be prefixed: `cpanelusername_learnsync`)
- [ ] Create a new MySQL user (will be prefixed: `cpanelusername_lsuser`)
- [ ] Set a strong password for the DB user — save it securely
- [ ] Add the user to the database with **ALL PRIVILEGES**
- [ ] Note your DB name, DB user, DB password, and host (`localhost`)

### 2.2 — Verify MySQL version
- [ ] In cPanel → **phpMyAdmin** → check the MySQL/MariaDB version shown at login
- [ ] **Minimum required:** MySQL 5.7.8 or MariaDB 10.2.7 (for `JSONField` support)
- [ ] If version is older, raise with hosting support — upgrade or switch hosts

---

## Phase 3 — Python App Setup in cPanel

### 3.1 — Create the Python application
- [ ] cPanel → **Setup Python App** (or "Python Apps" depending on your host)
- [ ] Click **Create Application**
- [ ] **Python version:** select 3.10, 3.11, or 3.12 (3.11 recommended)
- [ ] **Application root:** the directory where you uploaded the project files (e.g. `learnsync`)
- [ ] **Application URL:** your domain or subdomain (e.g. `yourdomain.com` or `app.yourdomain.com`)
- [ ] **Application startup file:** `passenger_wsgi.py`
- [ ] **Application Entry point:** `application`
- [ ] Save / Create

### 3.2 — Upload files
- [ ] Upload the project files to the Application Root via **File Manager** or FTP/SFTP
- [ ] Verify `passenger_wsgi.py` and `manage.py` are at the root of the Application Root

---

## Phase 4 — Virtual Environment & Dependencies

### 4.1 — Activate the cPanel-managed venv
cPanel creates the venv automatically when you set up the Python app.
Use the **Enter to the virtual environment** command shown in the Python App panel, or SSH in:

```bash
source /home/USERNAME/virtualenv/APPNAME/3.11/bin/activate
cd /home/USERNAME/APPROOT
```

### 4.2 — Install dependencies
- [ ] `pip install --upgrade pip`
- [ ] `pip install -r requirements.txt`
- [ ] Verify no errors. Common failures:
  - **mysqlclient compile error** → see Troubleshooting § MySQL driver below
  - **pillow compile error** → `pip install pillow --no-binary :all:` or contact host

### 4.3 — Confirm mysqlclient installed
- [ ] `python -c "import MySQLdb; print(MySQLdb.__version__)"` — should print a version

---

## Phase 5 — Environment Configuration

### 5.1 — Create `.env` on the server
- [ ] Copy `.env.production` → `.env` in the Application Root
- [ ] Fill in every variable:
  - `SECRET_KEY` — generate with `python -c "import secrets; print(secrets.token_hex(50))"`
  - `DEBUG=False`
  - `ALLOWED_HOSTS` — your domain(s), comma-separated
  - `CSRF_TRUSTED_ORIGINS` — `https://yourdomain.com` (include scheme)
  - `DB_ENGINE=django.db.backends.mysql`
  - `DB_NAME` — the full prefixed name from Phase 2
  - `DB_USER` — the full prefixed username from Phase 2
  - `DB_PASSWORD` — the password from Phase 2
  - `DB_HOST=localhost`
  - `DB_PORT=3306`

### 5.2 — Verify settings
- [ ] `python manage.py check --deploy` — must pass with no errors
  - Expected warnings about HSTS/HTTPS are OK if you haven't set up SSL yet;
    resolve them after SSL certificate is installed (Phase 8)

---

## Phase 6 — Database Migration

### 6.1 — Run migrations
- [ ] `python manage.py migrate`
- [ ] Confirm all 16+ selfpaced migrations applied without errors
- [ ] Check for warnings about `JSONField` — none expected on MySQL 5.7.8+

### 6.2 — Create superuser
- [ ] `python manage.py createsuperuser`
- [ ] Save the username and password securely

### 6.3 — Seed initial data (if needed)
- [ ] `python manage.py sp_ingest` or manually create Programmes via Django admin
- [ ] Add monitored countries via `/admin/countries/`
- [ ] Set up eHub pattern registry via `/admin/pattern-registry/`

---

## Phase 7 — Static Files

### 7.1 — Static files strategy
WhiteNoise serves static files directly from Django (no separate web-server config needed).
It's already enabled via `whitenoise.middleware.WhiteNoiseMiddleware`.

- [ ] Confirm `staticfiles/` was uploaded (from `collectstatic` in Phase 1)
- [ ] OR re-run on the server: `python manage.py collectstatic --no-input`
- [ ] Verify `STATIC_ROOT` points to `staticfiles/` (it does by default)

### 7.2 — Optional: serve statics via Apache directly
If your host allows `.htaccess` aliasing (faster than WhiteNoise on shared hosting):
- [ ] Add to `.htaccess` in your public root:
  ```apache
  Alias /static/ /home/USERNAME/APPROOT/staticfiles/
  ```
- [ ] This is optional — WhiteNoise works fine without it

---

## Phase 8 — SSL & Security

### 8.1 — Install SSL certificate
- [ ] cPanel → **SSL/TLS** → install Let's Encrypt (AutoSSL) or upload your certificate
- [ ] Confirm the site loads at `https://yourdomain.com`

### 8.2 — Enable security headers
Once HTTPS is confirmed working:
- [ ] In `.env`, ensure `DEBUG=False` (already set)
- [ ] `CSRF_TRUSTED_ORIGINS` is set (already done in Phase 5)
- [ ] The settings already enable `SECURE_SSL_REDIRECT`, HSTS, and secure cookies
      when `DEBUG=False` — these activate automatically

### 8.3 — HTTPS redirect loop fix (if needed)
If you see a redirect loop after enabling SSL, cPanel's proxy may not send
`X-Forwarded-Proto`. The settings already include:
```python
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
```
If the loop persists, temporarily add to `.env`:
```
# SECURE_SSL_REDIRECT is always True when DEBUG=False — to disable it
# only during testing, you'd need a custom env var. Raise with host instead.
```

---

## Phase 9 — Smoke Tests

### 9.1 — Basic functionality
- [ ] Visit `https://yourdomain.com/` — login page appears
- [ ] Log in with the superuser created in Phase 6
- [ ] Dashboard loads (may be empty — that's fine)
- [ ] `/admin/upload/` — upload a test CSV; confirm job processes
- [ ] `/learners/` — learner list loads
- [ ] `/reports/manager/?format=excel` — Excel file downloads
- [ ] `/reports/manager/?format=pdf` — PDF file downloads
- [ ] `/analytics/` — charts render (may be empty without data)

### 9.2 — Error pages
- [ ] Visit a non-existent URL (e.g. `/does-not-exist-xyz/`) — you should see the **custom 404 page** (branded LearnSync card), not Django's yellow debug page
- [ ] Manually trigger a 403: try submitting a form with a missing CSRF token — branded 403 page appears
- [ ] Error pages load without static files (they use Google Fonts CDN, no `{% load static %}` — safe even if collectstatic was skipped)

### 9.3 — Check error log
- [ ] cPanel → **Errors** or check the Passenger log file
- [ ] No Python tracebacks or `500 Internal Server Error` entries

### 9.4 — Confirm DEBUG is off
- [ ] Visit a non-existent URL — custom branded 404, NOT Django's yellow error page
- [ ] `python manage.py check --deploy` returns no errors

---

## Phase 10 — Ongoing Operations

### 10.1 — Restarting the app
After any code update, you must restart Passenger:
- [ ] cPanel → **Setup Python App** → click **Restart** next to your app
- [ ] Or touch the `restart.txt` file: `touch tmp/restart.txt` (if Passenger supports it)

### 10.2 — Applying future migrations
```bash
source /home/USERNAME/virtualenv/APPNAME/3.11/bin/activate
cd /home/USERNAME/APPROOT
python manage.py migrate
# Then restart the app in cPanel
```

### 10.3 — Updating static files
After any template or CSS change:
```bash
python manage.py collectstatic --no-input
# Restart the app
```

### 10.4 — Background job note
The app uses in-process `threading.Thread` for CSV ingestion jobs.
On cPanel shared hosting:
- Jobs run inside the Passenger worker process — they work fine for normal uploads
- If a job is mid-run when you restart the app, it is automatically marked `failed`
  on next startup (handled by `selfpaced/apps.py → SelfpacedConfig.ready()`)
- For very large CSVs, the worker may time out. Split large uploads into smaller files.
- If you outgrow threading, the future path is Celery + Redis (separate ticket)

---

## Troubleshooting

### MySQL driver: mysqlclient won't compile
If `pip install mysqlclient` fails with a C compiler error:

**Option A — Ask hosting support** to pre-install `libmysqlclient-dev` on the server.

**Option B — Use PyMySQL (pure Python fallback)**:
1. `pip install PyMySQL`
2. Uncomment these two lines in `passenger_wsgi.py`:
   ```python
   import pymysql
   pymysql.install_as_MySQLdb()
   ```
3. Remove `mysqlclient` from `requirements.txt` and add `PyMySQL>=1.1.0`
4. Restart the app

### JSONField error on migrate
`django.db.utils.OperationalError: (1091, "Can't DROP ... check that it exists")`
→ Usually a pre-existing partial migration. Run `python manage.py migrate --fake-initial`

`django.db.utils.NotSupportedError: JSONField is not supported on this database backend`
→ Your MySQL/MariaDB version is below 5.7.8/10.2.7. Upgrade the database.

### CSRF verification failed
Add your domain to `CSRF_TRUSTED_ORIGINS` in `.env`:
```
CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

### Static files return 404
- Confirm `collectstatic` was run and `staticfiles/` exists
- Confirm `STATIC_ROOT = BASE_DIR / 'staticfiles'` in settings (it is)
- WhiteNoise requires `DEBUG=False` to serve from `STATIC_ROOT`; in dev use `DEBUG=True`

### 500 on first load, logs show `ImproperlyConfigured: No module named X`
The cPanel venv is missing a package. Run `pip install -r requirements.txt` again,
then restart the app.

---

## Quick-Reference Commands (SSH)

```bash
# Activate venv
source /home/USERNAME/virtualenv/APPNAME/3.11/bin/activate

# From the app root:
python manage.py check --deploy
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --no-input
python manage.py shell   # interactive Django shell for debugging
```

---

*Checklist generated May 2026 — LearnSync v1.1 · Django 6 · MySQL*
