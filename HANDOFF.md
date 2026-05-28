# LearnSync — Technical Handoff Document

**Version:** 1.2  
**Generated:** May 2026  
**Status:** Current  
**Classification:** Internal

---

## Table of Contents

1. [App Overview](#1-app-overview)
2. [Technology Stack](#2-technology-stack)
3. [Project Structure](#3-project-structure)
4. [Running Locally](#4-running-locally)
5. [URL Map — Every Route](#5-url-map--every-route)
6. [Page-by-Page Reference](#6-page-by-page-reference)
7. [All Metric Definitions](#7-all-metric-definitions)
8. [Health Scoring Algorithm](#8-health-scoring-algorithm)
9. [Data Model Reference](#9-data-model-reference)
10. [Key Data Flows](#10-key-data-flows)
11. [Admin Capabilities](#11-admin-capabilities)
12. [Downloads — Excel & PDF](#12-downloads--excel--pdf)
13. [Known Constraints & Design Decisions](#13-known-constraints--design-decisions)

---

## 1. App Overview

LearnSync is a Django internal web application built for Learner Community Executives (LCEs) and Programme Managers to track, understand, and act on learner progress across self-paced programmes.

**What it does:**
- Ingests learner progress data from CSV exports of the staff portal
- Computes and stores health flags per learner per enrolment
- Provides searchable/filterable views of learner and programme health
- Tracks every intervention (call, email, message) logged by the team
- Generates a manager-facing report with Excel (9 sheets) and PDF (with charts) downloads

**What it does NOT do:**
- Host learning content or manage assignments (that's the LMS / eHub)
- Process payments
- Replace the staff portal

---

## 2. Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.x |
| Framework | Django 6.x |
| Database | SQLite (dev) / PostgreSQL (production recommended) |
| Frontend | Server-rendered HTML + Tailwind-adjacent CSS, Chart.js 4 for charts |
| Partial updates | HTMX (filter bar swaps on analytics page) |
| Excel export | openpyxl 3.1.5 |
| PDF export | ReportLab 4.5.1 (Platypus layout + Graphics charts) |
| Background jobs | Python `threading.Thread(daemon=True)` — in-process, no external queue |
| Template engine | Django templates |

---

## 3. Project Structure

```
LDP/
├── lce_platform/
│   ├── lce_platform/          # Django project (settings, urls, wsgi)
│   ├── selfpaced/             # The main app
│   │   ├── models.py          # All database models
│   │   ├── health.py          # Health flag computation (pure functions)
│   │   ├── engine.py          # Ingestion processing engine
│   │   ├── parsing.py         # CSV parsing and validation
│   │   ├── detector.py        # Programme/course detection from eHub class names
│   │   ├── pace.py            # Pod pace calculation
│   │   ├── tasks.py           # Background job runners
│   │   ├── signals.py         # Django signals (post-save hooks)
│   │   ├── apps.py            # AppConfig — auto-resets stalled jobs on startup
│   │   ├── admin.py           # Django admin registrations
│   │   ├── urls.py            # All URL patterns for the app
│   │   ├── filters.py         # django-filter FilterSet definitions
│   │   ├── forms.py           # Django forms
│   │   ├── exports.py         # CSV export helpers
│   │   ├── querysets.py       # Shared queryset helpers (real_learners_qs)
│   │   ├── utils.py           # Utility functions (safe_json etc.)
│   │   ├── tables.py          # django-tables2 table definitions
│   │   ├── views/
│   │   │   ├── home.py        # Dashboard / daily briefing
│   │   │   ├── learners.py    # Learner list + profile
│   │   │   ├── programmes.py  # Programme list + detail
│   │   │   ├── reports.py     # Manager report (HTML + Excel + PDF)
│   │   │   ├── analytics.py   # Analytics page (charts + cohort table)
│   │   │   ├── interventions.py # Intervention log + log form
│   │   │   ├── pods.py        # Pod list + detail + assignment
│   │   │   ├── portfolio.py   # Portfolio cross-programme view
│   │   │   ├── admin_views.py # Upload, ingestion log, admin tools
│   │   │   ├── programme_admin.py # Programme/course CRUD
│   │   │   ├── programme_structure.py # Programme structure upload
│   │   │   └── help.py        # Help page
│   │   ├── templatetags/
│   │   │   └── sp_filters.py  # Custom template filters
│   │   └── migrations/        # 26 migrations
│   └── templates/
│       ├── base.html          # Root base
│       ├── selfpaced/base.html # App base (nav sidebar)
│       ├── selfpaced/home.html
│       ├── selfpaced/learner_list.html
│       ├── selfpaced/learner_profile.html
│       ├── selfpaced/programme_list.html
│       ├── selfpaced/programme_detail.html
│       ├── selfpaced/analytics.html
│       ├── selfpaced/manager_report.html
│       ├── selfpaced/intervention_list.html
│       ├── selfpaced/pod_list.html
│       ├── selfpaced/pod_detail.html
│       ├── selfpaced/portfolio.html
│       ├── selfpaced/help.html
│       ├── selfpaced/_report_card.html  # Reusable summary card component
│       ├── selfpaced/admin/             # All admin-facing templates
│       └── ingestion/                   # Upload/job templates
└── HANDOFF.md  ← this file
```

---

## 4. Running Locally

```bash
cd lce_platform
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/`. All routes are under the `selfpaced` app with prefix `/`.

**Stalled job auto-reset:** On every server startup, `apps.py → SelfpacedConfig.ready()` resets any `IngestionJob`, `EnrolmentUploadJob`, or `PodImportJob` with `status='processing'` to `status='failed'` — these were mid-run when the server last restarted and would otherwise be stuck indefinitely.

---

## 5. URL Map — Every Route

| URL | View | Purpose |
|---|---|---|
| `/` | `home.home` | Dashboard / daily briefing |
| `/learners/` | `learners.learner_list` | Searchable learner table |
| `/learners/<email>/` | `learners.learner_profile` | Individual learner profile |
| `/programmes/` | `programmes.programme_list` | Programme health list |
| `/programmes/stats/` | `programmes.programme_list_stats` | HTMX stats fragment |
| `/programmes/charts/` | `programmes.programme_charts` | Programme charts fragment |
| `/programmes/<pk>/` | `programmes.programme_detail` | Individual programme detail |
| `/interventions/` | `interventions.intervention_list` | Intervention log |
| `/interventions/log/` | `interventions.log_intervention` | Log a single intervention |
| `/interventions/bulk/` | `interventions.bulk_log_intervention` | Bulk log for multiple learners |
| `/pods/` | `pods.pod_list` | Pod health overview |
| `/pods/<pk>/` | `pods.pod_detail` | Single pod member list |
| `/pods/<pk>/assign/` | `pods.assign_to_pod` | Assign learner to pod |
| `/pods/<pk>/recompute/` | `pods.recompute_pod_pace` | Recompute pod paces |
| `/pods/recompute-all/` | `pods.recompute_all_pod_paces` | Recompute all pods |
| `/portfolio/` | `portfolio.portfolio` | Cross-programme portfolio view |
| `/analytics/` | `analytics.analytics` | Analytics charts page |
| `/reports/manager/` | `reports.manager_report` | Manager report (HTML/Excel/PDF) |
| `/help/` | `help.help_page` | Help documentation |
| **Admin routes** | | |
| `/admin/` | `admin_views.admin_home` | Admin hub |
| `/admin/upload/` | `admin_views.upload_csv` | Upload staff portal CSV |
| `/admin/ingestion/` | `admin_views.ingestion_log` | Ingestion job list |
| `/admin/ingestion/<pk>/` | `admin_views.ingestion_job_detail` | Job detail |
| `/admin/ingestion/<pk>/progress/` | `admin_views.job_progress_fragment` | HTMX progress bar |
| `/admin/ingestion/<pk>/retry/` | `admin_views.retry_job` | Retry a failed job |
| `/admin/ingestion/<pk>/review/` | `admin_views.review_job` | Review flagged rows |
| `/admin/ingestion/<pk>/delete/` | `admin_views.delete_job` | Delete a job |
| `/admin/enrolment-csv/` | `admin_views.enrolment_upload_log` | Enrolment CSV job list |
| `/admin/enrolment-csv/upload/` | `admin_views.upload_enrolment_csv` | Upload enrolment CSV |
| `/admin/enrolment-csv/<pk>/review/` | `admin_views.review_enrolment_csv` | Review enrolment CSV |
| `/admin/enrolment-csv/<pk>/reprocess/` | `admin_views.enrolment_reprocess` | Reprocess enrolment job |
| `/admin/enrolment-csv/purge/` | `admin_views.enrolment_upload_purge` | Purge old enrolment jobs |
| `/admin/programmes/` | `programme_admin.programme_admin_list` | Programme CRUD list |
| `/admin/programmes/<pk>/edit/` | `programme_admin.programme_admin_edit` | Edit programme |
| `/admin/programme-structure/` | `programme_structure.upload_programme_structure` | Upload structure CSV |
| `/admin/programmes/<prog_pk>/courses/<course_pk>/edit/` | `programme_admin.course_edit` | Edit course |
| `/admin/programmes/<prog_pk>/courses/merge/` | `programme_admin.course_merge` | Merge duplicate courses |
| `/admin/countries/` | `admin_views.country_settings` | Monitored countries config |
| `/admin/countries/purge/` | `admin_views.purge_unmonitored_learners` | Remove unmonitored learners |
| `/admin/pattern-registry/` | `admin_views.pattern_registry` | eHub pattern registry |
| `/admin/recompute-health/` | `admin_views.recompute_health` | Trigger health recompute |
| `/admin/pod-import/` | `admin_views.pod_import_log` | Pod import job list |
| `/admin/pod-import/upload/` | `admin_views.upload_pod_csv` | Upload pod assignment CSV |
| `/admin/pod-import/<pk>/review/` | `admin_views.review_pod_csv` | Review pod import |

**Download modifiers** (query params on `/reports/manager/`):
- `?format=excel` → returns `.xlsx`
- `?format=pdf` → returns `.pdf`
- (no param) → HTML page

---

## 6. Page-by-Page Reference

### 6.1 Home — Dashboard (`/`)

**Purpose:** Daily briefing — what needs attention right now.

**Key metrics displayed:**
- Total paid learners with delta vs previous upload; "Unique Learners" counts individuals with eHub activity data (tooltip explains it may be higher than enrolment count because it includes learners in upcoming/future programmes)
- Health breakdown: Active, At-Risk, Dormant, Graduated, Not Started (learner-level, delta badges)
- Enrolment-level health breakdown; "Enrolments" tile tooltip explains one learner in N programmes = N enrolments
- Roster-only (not yet in eHub) count alongside enrolment total
- Activation rate, module activation rate, retention rate, graduation rate
- Badge count, certificate count
- Onboarding funnel (enrolled in prerequisite, completed prerequisite)
- Upcoming enrolments count
- Pending follow-ups count, new learners this week
- Data freshness indicator (last upload date, staleness warning if > 7 days)

**Programme Health Breakdown table** (between charts and Enrolments by Week):
Per-programme rows with columns: Programme code | In eHub (total with activity data) | Roster Only (enrolment CSV only, no activity) | Activated (%) | Active (%) | At Risk (%) | Dormant (%) | Not Started | Graduated (%). Each status count is a clickable link to the learner list pre-filtered to that programme + health status (`?programme=<pk>&health=<status>`). Uses PKs not codes because `LearnerFilter.programme` is a `ModelMultipleChoiceFilter`.

**Charts:**
- Stacked bar chart: health distribution per programme (Chart.js, 300px height)
- Bar chart: at-risk flag counts (Chart.js)
- Stacked bar chart: weekly enrolment timeline per programme (Chart.js)

**Data source:** `home.py` — queries `Learner`, `Enrolment`, `CourseEnrolment`, `EnrolmentSnapshot`, `IngestionJob`, `Intervention`, `Programme`, `Course`

---

### 6.2 Learner List (`/learners/`)

**Purpose:** Find any learner matching a set of criteria.

**Columns:** Name, email, country, programmes, overall health, flag count, payment status, days since last activity, last intervention date.

**Filters available:**
- Programme, health status, flag type, payment status
- Country, region
- Enrolment week (from/to) with cohort basis (effective date / enrolment date / FSOL)
- Days since last activity (range)

**Actions:** Click through to learner profile, export to CSV or Excel, bulk log intervention.

**Pagination:** Yes. Unpaid learners excluded by default unless payment filter explicitly set.

**Data source:** `learners.py` — `Learner` with prefetched `Enrolment` → `Programme`, `CourseEnrolment`

---

### 6.3 Learner Profile (`/learners/<email>/`)

**Purpose:** Full picture of a single learner.

**Sections:**
1. **Header** — name, email, country, overall health status, active flag badges, payment status
2. **Programme enrolments** — one card per programme: current course, health status, enrolment date, activation date, graduation status, active flags with detail (days inactive, stuck assignment name, etc.)
3. **Course detail** (expanded) — all courses with status, assignment-level accessed/submitted/passed breakdown
4. **Snapshot trend** — health status and progress across all uploads (requires snapshots to exist)
5. **Intervention history** — all interventions in reverse chronological order

**Actions:** Log new intervention, navigate to programme view.

---

### 6.4 Programme List (`/programmes/`)

**Purpose:** Programme-level health at a glance.

**Columns:** Programme code, name, total enrolled (paid), active, at-risk, dormant, graduated, not started, activation rate, graduation rate, days since last upload.

**Actions:** Click into programme detail.

---

### 6.5 Programme Detail (`/programmes/<pk>/`)

**Purpose:** Deep dive into a single programme.

**Sections:**
- Health distribution summary cards
- Course completion funnel (learners reached vs completed per course)
- Enrolment trend (weekly bar chart)
- At-risk flag breakdown for this programme
- Assignment difficulty table (by pass rate ascending)

---

### 6.6 Analytics (`/analytics/`)

**Purpose:** Cross-programme data exploration with filterable charts.

**Filters (HTMX-powered):** Country, programme (multi-select), health status (multi-select), date range (from/to), cohort type (effective / enrolment date / FSOL).

**Charts (Chart.js):**
1. **Health donut** — overall learner health distribution
2. **Programme bar (stacked + line overlay)** — enrolled by health status per programme + activated count as line overlay, activation rate in tooltip
3. **At-risk flags bar** — count per flag type
4. **Country bar** — top 20 countries by learner count
5. **Portfolio mix donut** — single-programme vs multi-programme learners
6. **Course distribution (per programme)** — learners currently on each course by health status + badges earned
7. **Cohort bar charts** (two: total + active/at-risk/dormant/graduated) — learners by enrolment week cohort
8. **Progression time series** (four charts): Daily active learners per programme, cumulative, plus course completions and first-module activations

**Cohort table:** Columns: week label, total, activated, activation_rate, active + active_rate, at_risk + at_risk_rate, dormant + dormant_rate, graduated + graduated_rate, not_started. Every status count cell is a clickable `<a>` link to the learner list pre-filtered by cohort date range + health status (`?enrol_from=DATE&enrol_to=DATE&cohort_basis=TYPE&health=STATUS`). Rates are shown as `N (X%)` inline.

**Key ORM patterns:**
- `_activated_ids` frozenset: `CourseEnrolment.is_passed=True` for the lowest `sequence_number` course in each programme
- `_eff_date = Greatest(Coalesce(enrolment_date, start_date), Coalesce(start_date, enrolment_date))` — effective enrolment date used for cohort bucketing
- Upcoming programmes excluded: `Q(programme__start_date__isnull=True) | Q(programme__start_date__lte=today)`

---

### 6.7 Manager Report (`/reports/manager/`)

**Purpose:** Comprehensive programme-wide management report — HTML, Excel (9 sheets), PDF (with charts).

**Summary cards:** Active Programmes, Paid Enrolled, Activated (+ rate), Retained (+ rate), Graduated (+ rate), Badges, Active, At Risk, Dormant.

**Sections:**
1. Unpaid exclusion notice
2. Summary cards (9 metrics)
3. Programme breakdown table
4. At-risk flags + Payment status (two-column)
5. Recent interventions (last 30 days, up to 20 shown, full list in Excel)
6. Metric glossary
7. Weekly Activity — overall 13-week breakdown with chart
8. Weekly Activity — per-programme collapsible breakdown
9. What's in the downloads

**Data sources:** `reports.py → _build_report_data()` — single function builds all data, called three times (HTML, Excel, PDF all call it independently).

**Base queryset** (paid, started, non-prerequisite programmes):
```python
base_qs = (
    Enrolment.objects
    .filter(programme__is_prerequisite=False, programme__is_active=True)
    .filter(Q(programme__start_date__isnull=True) | Q(programme__start_date__lte=today))
    .exclude(learner__payment_status=PaymentStatus.UNKNOWN)
)
```

---

### 6.8 Interventions (`/interventions/`)

**Purpose:** Full intervention log with follow-up queue.

**Columns:** Date, learner, programme, type, outcome, follow-up status, logged by.

**Filters:** Logged by, programme, type, outcome, date range, follow-up required.

**Actions:** Log new intervention, mark follow-up resolved, navigate to learner profile.

---

### 6.9 Pods (`/pods/`)

**Purpose:** Pod health overview — groups of learners committed to a target completion date.

**Pod pace statuses:** On Track, Behind, Significantly Behind, Ahead, Completed.

**Pace calculation (per learner per programme)** — computed by `selfpaced/pace.py → compute_pod_pace()`:

- `courses_completed` = CourseEnrolments with `status=COMPLETED` for this enrolment
- `courses_in_progress` = CourseEnrolments with `status=IN_PROGRESS`
- `total_courses` = `programme.total_courses_for_graduation` (falls back to `courses.filter(is_active=True).exclude(code='WALX').count()`)
- `courses_remaining` = `max(0, total_courses − courses_completed)`
- `current_pace` = `courses_completed / weeks_active` (c/week)
- `required_pace` = `courses_remaining / weeks_remaining` (c/week; `None` if past target date)
- `ratio` = `current_pace / required_pace` → drives pace status (Ahead >1.05 · On Track ≥(1−threshold) · Behind ≥0.6 · Significantly Behind <0.6)
- `courses_behind` = `min(expected_by_now − courses_completed, courses_not_yet_started)` where `courses_not_yet_started = total_courses − courses_completed − courses_in_progress`. **This cap means a learner on the last in-progress course always shows 0 courses behind**, never an inflated number.
- `projected_completion` = `today + (courses_remaining / current_pace)` weeks

---

### 6.10 Portfolio (`/portfolio/`)

**Purpose:** Cross-programme executive summary.

**Metrics:** Portfolio health rollup, programme comparison table, enrolment velocity, graduation pipeline, flag distribution, intervention activity.

---

### 6.11 Admin — Upload (`/admin/upload/`)

**Purpose:** Upload staff portal CSV to ingest learner progress.

**Flow:**
1. File validation (synchronous — returns immediately if invalid)
2. Programme/course detection via eHub class name pattern registry
3. Background thread processes: structure extraction → learner matching → progress upsert → health flag recomputation → snapshot creation → ingestion log entry
4. HTMX polling updates the progress bar in the UI without full page refresh
5. Rows that can't be auto-matched are flagged for admin review in the flagged row queue

**Job model:** `IngestionJob` — `status` ∈ `{pending, processing, complete, failed, cancelled}`

---

### 6.12 Admin — Enrolment CSV (`/admin/enrolment-csv/`)

**Purpose:** Upload a simplified enrolment CSV (name, email, programme, enrolment date) to bulk-create enrolments without a full staff portal export.

**Job model:** `EnrolmentUploadJob`

---

### 6.13 Admin — Pod Import (`/admin/pod-import/`)

**Purpose:** Import pod assignment data from a CSV (typically exported from the Google Form collecting learner pod selections).

**Job model:** `PodImportJob`

---

## 7. All Metric Definitions

These are the canonical definitions used consistently across all views, tooltips, Excel, and PDF.

| Metric | Definition |
|---|---|
| **Paid Enrolled** | Learners with an active or pending payment enrolled in at least one programme. Learners with `payment_status = 'unknown'` (Unpaid) are excluded from every metric on the manager report. |
| **Activated** | Learners who have passed the first module of their programme — specifically, `CourseEnrolment.is_passed=True` for the course with the lowest `sequence_number` in the programme. This is a live query, not the stored `activation_date` field. |
| **Activation Rate** | `Activated ÷ Enrolled × 100`. The share of paid enrolled learners who have passed the first module. |
| **Retained** | Learners who passed the first module AND whose current `health_status` is `active`, `at_risk`, or `graduated`. They have not disengaged. |
| **Retention Rate** | `Retained ÷ Activated × 100`. Of learners who passed the first module, what percentage are still engaged? |
| **Graduated** | Learners who completed all required courses and met graduation criteria (`is_graduated=True`). |
| **Graduation Rate** | `Graduated ÷ Enrolled × 100`. End-to-end completion rate. |
| **Badges Earned** | Course-completion credentials awarded (`CourseEnrolment` with `status='completed'` in credential-awarding programmes). One badge per passed course — learners can earn multiple. |
| **Active** | Health status: on track, submitting work, passing, within all inactivity thresholds. No flags raised. |
| **At Risk** | Health status: one or more warning flags are active. Below dormancy threshold. |
| **Dormant** | Health status: no meaningful activity for > dormancy threshold days. Needs re-engagement. |
| **Graduated** (health) | Health status: programme completed. |
| **Not Started** | Health status: enrolled (and paid) but no first sign of life date. No activity of any kind. |
| **Never Activated** (flag) | Enrolled and has a FSOL date but has not accessed any assignment. Days since FSOL > activation threshold. |
| **Inactive** (flag) | Was previously active, has not accessed or submitted any assignment within inactivity threshold days. |
| **Stuck on Assignment** (flag) | Accessed a specific assignment but has not submitted it within the stuck threshold days. |
| **Low Pass Rate** (flag) | Pass rate on submitted assignments has fallen below the configured pass rate threshold (default 70%). |
| **Stalled Between Courses** (flag) | Completed a course but has not started the next one within the inter-course threshold days. |
| **No Onward Progress** (flag) | Completed the last known course in the programme with no activity in any other programme enrolment. |
| **Payment Issue** (flag) | `payment_status` is anything other than `compliant`. Fires immediately, no threshold. |

### Payment Status Values

| Value | Label | Included in Metrics? |
|---|---|---|
| `compliant` | Compliant | ✅ Yes |
| `due_soon` | Due Soon | ✅ Yes |
| `grace_period` | Grace Period | ✅ Yes |
| `overdue` | Overdue | ✅ Yes |
| `unknown` | Unpaid | ❌ No — excluded from all manager report metrics |

### Weekly Breakdown Column Semantics

| Column | Source Date | What it counts |
|---|---|---|
| Enrolled | `max(enrolment_date, programme.start_date)` | New paid enrolments bucketed to effective start week |
| Activated | `CourseEnrolment.completion_date` | First-module completions that occurred in this calendar week |
| Active | Current `health_status`, bucketed by effective enrolment date | Of learners enrolled in this week's cohort, how many are currently Active |
| At Risk | Current `health_status`, bucketed by effective enrolment date | Of learners enrolled in this week's cohort, how many are currently At-Risk |
| Graduated | `Enrolment.graduation_date` | Graduations that occurred in this calendar week |
| Interventions | `Intervention.intervention_date` | All interventions logged in this calendar week (includes unpaid learners) |

---

## 8. Health Scoring Algorithm

**Source file:** `selfpaced/health.py`

Health is computed at ingestion time by `compute_enrolment_health()` for each enrolment touched by an upload. The function takes the enrolment, upload date, and pre-fetched related objects to avoid N+1 queries.

### Flags computed in order

```
1. never_activated   — has FSOL but no assignments accessed, days since FSOL > activation_threshold
2. inactive          — has prior activity, days since last activity > inactivity_threshold
3. stuck_on_assignment — accessed but not submitted, days since accessed > stuck_threshold
4. low_pass_rate     — submitted > 0, pass_rate < pass_rate_threshold
5. stalled_between_courses — course completed, next course not started, days > inter_course_threshold
6. stalled_progression — completed last known course, no activity in any other programme
```

`payment_issue` flag is applied by the engine layer after `compute_enrolment_health()` returns, not inside the health module.

### Health status rollup (from flags)

```
if is_graduated:                        → "graduated"
if no first_sign_of_life:               → "not_yet_started"
if days_since_activity > dormancy_threshold:   → "dormant"
if never_activated flag AND days_since_fsol > dormancy_threshold: → "dormant"
if any flags active:                    → "at_risk"
else:                                   → "active"
```

### Default thresholds (overridable per programme)

| Threshold | Default |
|---|---|
| activation_threshold_days | 3 |
| inactivity_threshold_days | 7 |
| dormancy_threshold_days | 14 |
| stuck_assignment_threshold_days | 5 |
| pass_rate_threshold_pct | 70 |
| inter_course_threshold_days | 5 |
| upload_warning_threshold_days | 7 |

### Graduation detection

Graduation is detected from the stored `is_graduated` field OR inferred dynamically: if ALL CourseEnrolments are `status='completed'` AND the count of completed course enrolments meets the programme's `total_courses_for_graduation`, the learner is treated as graduated even if the stored flag hasn't been updated yet.

---

## 9. Data Model Reference

### Core Models

#### `Learner`
Primary key: `email` (string). Master identity record.

Key fields: `first_name`, `last_name`, `country`, `region`, `payment_status` (choices: compliant / due_soon / grace_period / overdue / unknown), `overall_health_status` (rollup from enrolments), `phone_number`, `ehub_profile_url`, `lms_profile_url`.

#### `Programme`
Key fields: `code` (unique, e.g. "AICE"), `ehub_code` (alternative code used in eHub class names), `name`, `is_active`, `is_prerequisite` (True for onboarding/WALX tracks — excluded from all metrics), `start_date` (null = no defined start; future start_date = upcoming, excluded from reports), `end_date` (past end_date excluded from course distribution), `awards_credentials`, `awards_certificate`, `total_courses_for_graduation`.

#### `Course`
Key fields: `programme` (FK), `sequence_number` (ordering within programme), `full_name`, `code`, `expected_duration_days`, `is_active`.

**First module detection:** The course with the minimum `sequence_number` for a programme is the "first module". Activation = passing this course.

#### `Assignment`
Key fields: `course` (FK), `name`, `type` (milestone / test / other), `sequence_in_course`, `pass_threshold_pct`, `is_required_for_completion`.

#### `Enrolment`
One row per learner per programme. Primary health record.

Key fields: `learner` (FK), `programme` (FK), `enrolment_date`, `first_sign_of_life_date`, `activation_date` (stored but NOT used for the Activated metric — live first-module query is used instead), `current_course` (FK to Course), `health_status`, `active_flags` (JSONField: list of flag code strings), `is_graduated`, `graduation_date`, `has_activity_data` (bool — `True` if this enrolment came from an activity CSV upload; `False` for roster-only enrolments from the enrolment CSV. Used to split "In eHub" vs "Roster Only" counts on the home dashboard).

#### `CourseEnrolment`
One row per learner per course within a programme.

Key fields: `enrolment` (FK), `course` (FK), `status` (not_started / in_progress / completed / withdrawn), `completion_date`, `is_passed`, `last_activity_date`.

#### `AssignmentProgress`
One row per learner per assignment. Finest grain of data.

Key fields: `course_enrolment` (FK), `assignment` (FK), `is_accessed`, `accessed_date`, `is_submitted`, `submitted_date`, `is_passed`.

#### `Intervention`
One row per logged intervention.

Key fields: `learner` (FK), `enrolment` (FK, optional), `intervention_date`, `type` (call / email / sms / ehub_message / meeting / automated / other), `outcome` (re_engaged / no_response / promised_action / withdrew / not_applicable / other), `follow_up_required`, `follow_up_date`, `notes`, `logged_by` (FK to User).

#### `EnrolmentSnapshot`
Point-in-time snapshot per enrolment per upload.

Key fields: `learner` (FK), `enrolment` (FK), `programme` (FK), `ingestion_job` (FK), `health_status`, `courses_completed`, `pass_rate`, `last_activity_date`, `payment_status`.

### Job Models

All three job models follow the same pattern — `status` ∈ `{pending, processing, complete, failed, cancelled}`. All run in daemon threads. Stalled `processing` jobs are auto-reset to `failed` on startup (see `apps.py`).

| Model | Purpose |
|---|---|
| `IngestionJob` | Staff portal CSV upload job |
| `EnrolmentUploadJob` | Simplified enrolment CSV upload job |
| `PodImportJob` | Pod assignment CSV import job |

### Pod Models

| Model | Purpose |
|---|---|
| `Pod` | Programme + target completion month group |
| `PodAssignment` | Single learner's current pod membership, with pace fields |

### Config Models

#### `ProgrammeThreshold`
OneToOne with `Programme`. Stores per-programme threshold overrides. Falls back to `THRESHOLD_DEFAULTS` dict for any null field.

#### `ProgrammeIdentifierRegistry`
Maps raw eHub class name patterns or course name prefixes → `Programme` + `Course`. Used by the ingestion detector.

---

## 10. Key Data Flows

### 10.1 Staff Portal CSV Upload

```
User uploads CSV via /admin/upload/
    ↓
parsing.py — validates columns, date formats
    ↓ (sync — returns error immediately if invalid)
IngestionJob created with status='pending'
    ↓
Background thread started (daemon=True)
    ↓
engine.py processes file:
    1. detector.py — maps each row to Programme + Course via registry
    2. Unmatched rows → FlaggedRows, held for admin review
    3. Learner upsert (create or update Learner records)
    4. Progress upsert (AssignmentProgress create/update)
    5. CourseEnrolment status update
    6. health.py — compute_enrolment_health() for each touched enrolment
    7. EnrolmentSnapshot created for each active enrolment
    8. Learner.overall_health_status rolled up from enrolment statuses
    9. IngestionJob.status = 'complete'
```

### 10.2 Effective Enrolment Date

Used throughout reports and analytics to attribute pre-launch enrolments to the programme start week:

```python
_eff_date = Greatest(
    Coalesce('enrolment_date', 'programme__start_date'),
    Coalesce('programme__start_date', 'enrolment_date'),
)
# Result: max(enrolment_date, programme.start_date), null-safe
```

If a learner enrolled on Jan 15 but the programme started Mar 23, their enrolment is attributed to the week of Mar 23 in all weekly breakdown views.

### 10.3 Activated IDs Computation

Used in both `reports.py` and `analytics.py`:

```python
_activated_ids = frozenset(
    CourseEnrolment.objects
    .filter(
        enrolment__in=base_qs,
        is_passed=True,
        course__sequence_number=Subquery(
            Course.objects.filter(programme_id=OuterRef('enrolment__programme_id'))
            .order_by('sequence_number').values('sequence_number')[:1]
        ),
    )
    .values_list('enrolment_id', flat=True)
    .distinct()
)
```

The subquery finds the minimum `sequence_number` course per programme, then checks if the learner has `is_passed=True` for that course. Result is a frozenset of enrolment PKs used in subsequent `Count(..., filter=Q(pk__in=_activated_ids))` annotations.

### 10.4 Health Recomputation

Can be triggered manually from `/admin/recompute-health/`. Runs `compute_enrolment_health()` for all active enrolments without creating new snapshots. Use after changing thresholds or fixing programme structure.

---

## 11. Admin Capabilities

| Capability | Location |
|---|---|
| Upload staff portal CSV | `/admin/upload/` |
| Review/resolve flagged rows | `/admin/ingestion/<pk>/review/` |
| Retry or delete an ingestion job | `/admin/ingestion/<pk>/` |
| Upload enrolment CSV (bulk enrol) | `/admin/enrolment-csv/upload/` |
| Upload pod assignment CSV | `/admin/pod-import/upload/` |
| Create/edit programmes | `/admin/programmes/` |
| Edit course details | `/admin/programmes/<pk>/courses/<pk>/edit/` |
| Merge duplicate courses | `/admin/programmes/<prog_pk>/courses/merge/` |
| Upload programme structure CSV | `/admin/programme-structure/` |
| Edit eHub pattern registry | `/admin/pattern-registry/` |
| Configure monitored countries | `/admin/countries/` |
| Purge learners from removed countries | `/admin/countries/purge/` |
| Trigger full health recompute | `/admin/recompute-health/` |

---

## 12. Downloads — Excel & PDF

### Excel (9 sheets)

| Sheet | Contents |
|---|---|
| Summary | Key metrics with inline definitions + health breakdown |
| By Programme | Full row per programme: enrolled, activated, act%, retained, ret%, active, at-risk, dormant, graduated, grad%, badges |
| Learner Detail | One row per paid enrolled learner with all key dates and health status |
| Payment Status | Distribution across all learners (including unpaid) |
| At-Risk Flags | Flag counts and descriptions |
| Recent Interventions | Last 30 days |
| Metric Definitions | Full glossary |
| Weekly Breakdown | Last 13 weeks overall: enrolled, activated, active, at-risk, graduated, interventions |
| Weekly by Programme | Same 13-week breakdown, one row per (programme × week) |

### PDF (landscape A4)

Sections in order:
1. Title + generation date + unpaid exclusion note
2. Performance Summary table
3. Health Status table + **horizontal bar chart** (colour-coded by status)
4. Programme Breakdown table + **grouped vertical bar chart** (Enrolled / Activated / Graduated per programme)
5. At-Risk Flag Breakdown table
6. Payment Status Distribution table
7. Weekly Activity table + **line chart** (Enrolled / Activated / Active / At-Risk / Graduated over 13 weeks)

---

## 13. Known Constraints & Design Decisions

### Background Jobs (no external queue)
All three job types run in `threading.Thread(daemon=True)` — in-process threads, no Celery/RQ. This is simple and adequate for the current team size but means:
- A server restart kills any running job (auto-reset to `failed` on next startup)
- Long uploads block the thread pool if the server is under concurrent load

If volume grows, migrate to Celery + Redis.

**Cross-job-type concurrency:** `IngestionJob` (activity CSV) and `EnrolmentUploadJob` (enrolment CSV) use **separate guards** — each type only blocks a second job of its own type. They can run simultaneously in different threads. In practice this is safe because activity-CSV learners (`has_activity_data=True`) and enrolment-CSV learners (roster-only, `has_activity_data=False`) are almost always different populations. The only risk: if the same learner appears in both files simultaneously, whichever job finishes last wins on shared Learner/Enrolment fields, and health computed by the ingestion job may not see the roster-only enrolment if the enrolment job hasn't finished yet. **Best practice: run the enrolment CSV first, then the activity CSV.**

### Activation Metric vs `activation_date` Field
`Enrolment.activation_date` is a stored field set during ingestion. It reflects the old definition of activation (which was different). The **current** Activated metric is computed live from `CourseEnrolment.is_passed=True` for the first-module course. The stored `activation_date` field is still shown on the Learner Detail Excel sheet for historical reference but is NOT used for any metric calculation.

### `PaymentStatus.UNKNOWN = 'unknown'`
This is the "Unpaid" status. All manager report metrics use `base_qs` which excludes `.exclude(learner__payment_status=PaymentStatus.UNKNOWN)`. The label displayed in the UI is "Unpaid" but the database value is `'unknown'`.

### Upcoming Programmes Excluded
`Programme` records with `start_date > today` are "upcoming". The `base_qs` in reports and `enrolment_qs` in analytics both filter these out. Learners enrolled in upcoming programmes show as `upcoming_enrolment_count` on the home dashboard but don't appear in any health metrics.

### `_as_date()` Helper
`TruncWeek` returns a `datetime` on some databases (e.g. PostgreSQL) and a `date` on others (SQLite). The `_as_date(v)` helper (`v.date() if hasattr(v, 'hour') else v`) normalises this for dict lookups.

### Prerequisite Programmes
Programmes with `is_prerequisite=True` (e.g. WALX onboarding) are excluded from all enrolment metrics, health rollups, and report views. They appear only in the onboarding funnel on the home dashboard.

### Email as Primary Key
`Learner.email` is the primary key. If a learner changes their email they will appear as a new learner. No automatic deduplication exists — admins can merge records via Django shell if needed.

### Snapshot System
`EnrolmentSnapshot` records are created once per upload per active enrolment. They are used for:
- Delta comparisons on the home dashboard (current vs previous upload)
- Historical trend charts on the learner profile
They are NOT used for any current-state metrics (which always query live `Enrolment`/`CourseEnrolment` data).

---

*End of Handoff Document*  
*Last updated: May 2026*
