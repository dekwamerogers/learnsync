# LCE Learner Platform — Product Requirements Document
**Version:** 1.1  
**Status:** Draft  
**Audience:** Internal — Developer / LCE Team  
**Last Updated:** 2026-04-19

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Problem Statement](#2-problem-statement)
3. [Scope](#3-scope)
4. [Users & Roles](#4-users--roles)
5. [Core Definitions](#5-core-definitions)
6. [Funnel Stages](#6-funnel-stages)
7. [At-Risk Flags](#7-at-risk-flags)
8. [Ingestion System](#8-ingestion-system)
9. [Dashboard — Portfolio View](#9-dashboard--portfolio-view)
10. [Dashboard — Cohort View](#10-dashboard--cohort-view)
11. [Snapshot Export](#11-snapshot-export)
12. [Data Model Overview](#12-data-model-overview)
13. [Non-Functional Requirements](#13-non-functional-requirements)
14. [Decisions Log](#14-decisions-log)
15. [Out of Scope — V1](#15-out-of-scope--v1)
16. [Deferred to V1.2](#16-deferred-to-v12)
17. [Glossary](#17-glossary)

---

## 1. Purpose

The LCE Learner Platform is a purpose-built internal web application that consolidates learner tracking, engagement monitoring, and performance reporting into a single unified system. It replaces a fragmented set of disconnected spreadsheets with a structured, automated, and scalable platform.

Once built, the platform requires no technical knowledge to operate. Data ingestion, funnel stage computation, and at-risk flagging are fully automated. The team's job is acting on the data, not managing it.

---

## 2. Problem Statement

The LCE team currently tracks learner data across multiple disconnected spreadsheets — one per cohort, one per initiative, others for payment and engagement. This has several critical failure modes:

- **No single source of truth** — data is scattered across programmes and cohorts
- **Manual re-entry** — data is copied by hand each week, creating inconsistencies
- **Programme silos** — learners enrolled in multiple programmes have no unified view
- **No trend visibility** — without week-on-week history it is impossible to measure whether interventions are working
- **Person-dependent** — the system relies on whoever built each tracker; institutional knowledge leaves with people

---

## 3. Scope

### 3.1 V1 — In Scope

- Programme and cohort configuration UI
- Weekly CSV ingestion with fuzzy column matching and manual verification
- Background job processing for ingestion with atomic batch database updates
- Funnel stage computation engine (automated on every ingestion)
- At-risk flag computation engine (all 5 flags, automated on every ingestion)
- Portfolio view dashboard with summary bar
- Cohort view dashboard with learner table
- Closed cohorts tab
- Snapshot export — PNG or PDF for portfolio and cohort views
- Excel and CSV export from cohort view
- Audit log for all ingestions

### 3.2 Out of Scope — V1
See [Section 15](#15-out-of-scope--v1).

### 3.3 Deferred to V1.2
See [Section 16](#16-deferred-to-v12).

---
## 4. Users & Roles

| Role | Description | Primary Need |
|---|---|---|
| LCE (Learner Community Executive) | Core platform user | Monitor cohort health, identify at-risk learners, generate reports |
| Programme Manager | Oversight of programme performance | Review programme-level funnel rates and graduation metrics |
| Leadership / Finance | Executive reporting | Portfolio health summary, revenue proxy metrics |

> **Note:** Call centre rep and learner portal roles are deferred to V1.2.

---

## 5. Core Definitions

### 5.1 Unique Learner

A learner is one unique email address in the system. Email must be cleaned — no spaces, all lowercase. Duplicate emails must be removed. One person equals one learner regardless of how many programmes they are enrolled in.

A learner is only counted as a unique learner if they are **paid-enrolled** in at least one programme.

### 5.2 Programme

A structured learning track with defined submission requirements, a required graduation percentage, and a defined checkpoint rhythm.

### 5.3 Cohort

A time-based group within a programme. A learner may join multiple cohorts across programmes or rejoin the same programme in a new cohort.

### 5.4 Enrollment

The relationship between one learner, one programme, and one cohort. A learner can have multiple enrollments. Enrollments are only operationally active if the learner is **paid-enrolled**.

### 5.5 Paid Enrollment

A learner is considered paid-enrolled if their payment status column — configured per programme — resolves to the defined paid value. Payment status configuration is set once per programme during setup and covers:

- Which column indicates payment status
- What data type it is (boolean, date, numeric)
- What value means paid (e.g. `Yes`, any non-null date, `1`)

Unpaid learners may exist in the CSV but are excluded from all funnel computation, at-risk flagging, and revenue metrics.

### 5.6 Juggler

A learner who is paid-enrolled in more than one active programme simultaneously.

### 5.7 Cohort States

| State | Description |
|---|---|
| Active | Currently running. Weekly uploads expected. All funnel and at-risk logic runs. |
| Grace Period | Cohort end date has passed but graduation has not been finalised. Uploads still accepted. Full at-risk flags remain active. Graduation status column is added. |
| Closed | Graduation confirmed. Data retained for historical reporting. Corrective CSV uploads are still permitted. |

All cohort state transitions are triggered **manually** by an LCE or Admin. There is no auto-closing.

### 5.8 Onboarded

A learner is considered onboarded if they appear in the LMS dataset or their onboarded status is marked as TRUE. For V1 purposes, onboarded is equivalent to paid-enrolled.

### 5.9 Revenue Proxy

Since direct payment data is unavailable, paid enrollment is used as a revenue proxy.

- **Paying Learner (Proxy):** A learner who is paid-enrolled in at least one programme.
- **Revenue-Eligible Learner:** A learner who is paid-enrolled AND Active in at least one programme.
- **Unique Active Paying Learners:** The primary revenue proxy metric. Distinct email addresses who are Active in at least one programme. Each person counted once.

---

## 6. Funnel Stages

All funnel stages are computed automatically on each data ingestion. They are calculated per learner per programme. Only paid-enrolled learners enter the funnel.

```
Paid Enrolled → Activated → Active ──┬── On-Track
                                      └── At-Risk
```

### 6.1 Enrolled

Learner appears in the dataset for a specific programme and cohort but has **not yet paid**. They are visible in the cohort view so LCEs know who to contact, particularly during activation weeks. They are excluded from all funnel metrics, at-risk flags, and revenue calculations.

Enrolled learners are displayed in the cohort table with a clear "Unpaid" badge. They can be filtered to using the "Enrolled (unpaid)" stage filter. The summary bar counts remain paid-only.

### 6.2 Activated

A learner is considered Activated once they have met the **activation submission threshold** for their programme. What counts and when it must happen are both programme-level configuration:

- **`activation_week`** — the week by which the first submission must have occurred for the learner to be considered Activated (default: 1). Set per programme.
- **`activation_submission_type`** — what type of submission satisfies activation (default: `milestone`). Set per programme. Options:

| Value | Meaning |
| --- | --- |
| `milestone` | At least one milestone submission |
| `quiz` | At least one quiz submission |
| `any` | At least one submission of either type |
| `both` | At least one milestone AND at least one quiz |

A learner who has not met the activation threshold by the activation week is classified as Inactive, not Enrolled — they are in the dataset but have not engaged.

### 6.3 Active

Learner is Activated AND currently has any engagement signal. Active is a **parent category** that contains both On-Track and At-Risk learners. A learner does not leave Active when they become At-Risk — they remain Active and are flagged.

### 6.4 On-Track

Learner is Active AND has completed the required quizzes and tests for all previous weeks. This is the healthy Active state.

**One-week leeway:** The on-track threshold is evaluated against the *previous* week's expected submission count (`expected_at_week(week - 1)`), not the current week's. This prevents a newly-released milestone from immediately dropping learners off On-Track in the same week the milestone is published — the team typically uploads data the same week new content is released.

**Catch-up weeks:** When a cohort enters weeks where no new milestones are released, the expected count does not increase. The system caps the expected count at the total number of milestones released so far (derived from `CohortItem` records). Weeks 7 and 8 of AiCE, for example, expect the same 3 milestones as week 5 — learners in those weeks are measured against what has actually been released, not an inflating linear count.

### 6.5 At-Risk

Learner is Active AND has triggered one or more of the 5 at-risk flags. See [Section 7](#7-at-risk-flags).

### 6.6 Inactive

Learner has zero submissions and no progress recorded. They were never Activated.

### 6.7 Stalled

Learner was previously On-Track but has since dropped. Captured by At-Risk Flag 4.

### 6.8 Grace Period Additions

When a cohort enters Grace Period, the following is added alongside the standard funnel:

- **Graduation status column** — Graduated / Not Yet / Pending
- Full at-risk flags remain active (since there is still time to push learners to graduate)

---

## 7. At-Risk Flags

Each flag is computed independently on every ingestion. A learner can have multiple flags active simultaneously. On the cohort view, only the **highest priority active flag** is displayed per learner row, using increasing shades of red to indicate severity. All active flags are visible on the learner profile.

### Flag Priority Order (Highest to Lowest)

| Priority | Flag | Definition | Recommended Action |
|---|---|---|---|
| 1 | **No Submissions** | Learner is Activated but has not submitted any new work of any type since their activation week. Their total submission count (`total_submissions_all`) at the current week equals their total at the week they first activated — nothing has been added since, across milestones, quizzes, or assignments. Complete disengagement post-activation. | Immediate outreach to re-engage. |
| 2 | **Two Weeks Behind** | Learner's submission count is below the expectation for two checkpoint periods. | Provide guidance, reminders, or support sessions. |
| 3 | **Dropped from On-Track** | Learner was previously On-Track but is now 3 or more weeks behind the current checkpoint expectation. | Investigate causes, re-motivate learner. |
| 4 | **Below Graduation Requirement** | Learner's progress percentage is below the required graduation level at the current checkpoint. | Encourage catch-up work or tutoring. |
| 5 | **Long LMS Inactivity** | Learner has not logged into the LMS for X or more days since last activity. X is configurable per programme. Default: 14 days. Only fires for Activated learners. Only applies to programmes where the LMS activity column is mapped. | Contact to re-engage with platform. |

> **Implementation note:** Flag 5 requires the LMS last login date column to be configured in the programme's column mapping. If this column is not present for a programme, Flag 5 is marked as not applicable — it does not fire or error.

---

## 8. Ingestion System

### 8.1 Overview

LCEs upload a weekly CSV export from the LMS. The ingestion engine parses the file, matches learners, creates or updates records, and runs funnel and at-risk computation automatically. The LCE does not need to classify learners manually.

### 8.2 Upload Flow

```
Upload CSV → Auto-detect Programme & Cohort → Verify → Confirm → Background Processing → Summary
```

**Step 1 — Upload**  
LCE uploads the CSV file via the platform UI.

**Step 2 — Auto-detect**  
The system attempts to derive the programme and cohort from:
1. The filename (primary) — e.g. `GraphicDesign_VAC16_Week3.csv`
2. Metadata within the file itself (secondary) — e.g. a programme or cohort field in the file header

If neither yields a confident match, the verification screen shows empty dropdowns for the LCE to fill in manually.

**Step 3 — Verify**  
The LCE sees a verification screen showing:
- Detected programme name (editable dropdown)
- Detected cohort name (editable dropdown)
- Column mapping — all detected field mappings listed, with ability to correct any ambiguous or unmatched columns
- Payment status column and value configuration
- LMS activity column (if applicable)

Confirmed mappings are saved per programme so future uploads auto-fill without repeating verification.

**Step 4 — Confirm**  
LCE confirms and submits. The system immediately returns a processing status to the UI. The LCE does not wait for processing to complete.

**Step 5 — Background Processing**  
Processing starts immediately in a background thread within the web server process. No separate worker process is required. The ingestion engine:
1. Parses and validates the entire CSV in memory
2. Opens a single database transaction
3. Bulk upserts all records atomically — if anything fails, the entire upload rolls back cleanly
4. Commits
5. Triggers funnel and at-risk computation as a follow-on background thread

**Step 6 — Summary**  
On completion, the LCE sees a post-ingestion summary:
- Total rows processed
- Rows skipped (with downloadable list of skipped rows for investigation)
- Whether this was an overwrite of an existing snapshot
- Timestamp and upload reference

### 8.3a Upload History

All ingestion jobs are listed at `/ingestion/jobs/`. This page is persistent — the LCE can navigate away and return to see job progress at any time. The navbar shows a pulsing badge with the count of active jobs.

Each row in the upload history shows: cohort, week, filename, status, rows processed, rows skipped, who uploaded, start time, and finish time.

- **Queued / Processing** jobs auto-refresh every 3 seconds via HTMX.
- **Run now** — if a job is stuck pending (e.g. server was restarted), a "Run now" button runs it synchronously.
- **Revert** — any completed or failed upload can be reverted. Revert deletes all `WeeklySnapshot` records for that cohort + week, resets all paid enrollment funnel stages to `enrolled`, and removes the `IngestionLog`. A confirmation dialog is shown before reverting. The same action is available as a bulk admin action on the `IngestionLog` admin page.

### 8.3 Column Mapping & Fuzzy Matching

Each programme may have a different CSV format. The ingestion engine uses fuzzy matching to automatically map CSV column headers to schema fields.

- Fuzzy matching runs on upload with a reasonable confidence threshold
- Columns below the threshold are flagged for manual resolution on the verification screen
- The unique learner identifier is always **email address** — this is the anchor field
- All other field mappings (submissions, progress %, payment status, LMS activity) are matched per programme
- Saved mappings are applied automatically on future uploads for the same programme

### 8.4 Learner Matching

- Learners are matched by email address
- Email is cleaned before matching — stripped of whitespace, converted to lowercase
- Duplicate emails within a single CSV are deduplicated

### 8.5 Bad Row Handling

Bad rows (malformed email, missing required fields, unparseable values) are **silently skipped**. They are included in the post-ingestion summary as a downloadable list. The ingestion does not fail on bad rows.

### 8.6 Snapshot Overwrite

If a CSV is uploaded for a week that already has a snapshot, the snapshot is **overwritten**. The ingestion log records:
- Who uploaded
- When
- Whether it was an overwrite
- Whether the target cohort was in a closed state at the time (late correction flag)

Overwriting a closed cohort is permitted. The audit trail makes late corrections visible.

### 8.7 Cohort Lifecycle & Uploads

| Cohort State | Uploads Accepted | Funnel Computed | At-Risk Flags |
|---|---|---|---|
| Active | Yes | Yes | All 5 flags |
| Grace Period | Yes | Yes | All 5 flags + Graduation status |
| Closed | Yes (corrective) | Yes | All 5 flags + Graduation status |

### 8.8 Upload Trigger

V1: Manual upload by LCE via the platform UI.

> **Future provision (V1.2+):** Automated scheduled pull from a shared Google Drive folder. The ingestion module and data model are structured to support this without rearchitecting.

### 8.9 Programme Configuration (Setup)

Each programme is configured once before its first upload. Configuration covers:

- Programme name
- Whether the programme tracks quizzes (`tracks_quizzes = TRUE/FALSE`)
- Payment status column name, data type, and paid value
- LMS activity column name (if applicable)
- Flag 5 inactivity threshold in days (default: 14, programme-configurable)
- Cohort calendar: start date, end date, graduation requirement %
- CohortItem list: all milestones and quizzes with their release week

---

## 9. Dashboard — Portfolio View

### 9.1 Purpose

The portfolio view is the LCE's default landing page. It gives a full health overview of every active cohort across all programmes on a single screen. This is the primary Monday morning reporting view.

### 9.2 Page Structure

```
Summary Bar (headline metrics)
─────────────────────────────
Filter Bar (date / year / programme)
─────────────────────────────
Tabs: [ Active ] [ Closed ]
─────────────────────────────
Cohort Table (one row per cohort)
```

### 9.3 Summary Bar

Displayed at the top of the page. All numbers reflect the currently active filter.

| Metric | Description |
|---|---|
| Total Paid Enrolled | Sum across all visible cohorts |
| Ever Activated | Total who have met the activation threshold (includes On-Track, At-Risk, and Activated sub-stage learners). Labelled "Ever Activated" to distinguish from the narrow `activated` funnel sub-stage. |
| Total Active | Sum across all visible cohorts |
| Total At-Risk | Sum across all visible cohorts |
| Jugglers | Count of learners paid-enrolled in more than one active programme. Clickable — navigates to Juggler Page (V1.2). |
| Graduation Target | Aggregate progress toward the graduation target across all visible cohorts. For active cohorts shows On-Track vs target; for grace/closed cohorts shows Graduated vs target. Green if on track, yellow if below. Includes a mini progress bar. |

### 9.4 Filter Bar

| Filter | Options |
|---|---|
| Year | Select year — shows cohorts where start date OR end date falls within that year |
| Date Range | Custom start and end date — same inclusive logic as year filter |
| Programme | Filter by specific programme name |

### 9.5 Tabs

| Tab | Content |
|---|---|
| Active (default) | Cohorts in Active or Grace Period state |
| Closed | Cohorts in Closed state |

### 9.6 Cohort Row Structure

Each cohort is displayed as one row. Columns:

**Identity**

| Column | Detail |
|---|---|
| Programme Name | Clickable — navigates to Programme View (V1.2) |
| Cohort Name | Clickable — navigates to Cohort View |
| State Badge | Visual badge — Active (one style) or Grace Period (distinct colour) |
| Current Week | Week number within the cohort calendar |

**Funnel Counts & Rates**

| Column | Count | Rate (denominator) |
|---|---|---|
| Paid Enrolled | Count | — (this is the denominator) |
| Activated | Count | % of Paid Enrolled |
| Active | Count | % of Paid Enrolled |
| On-Track | Count | % of Active |
| At-Risk | Count | % of Active |

**Graduation**

| Column | Detail |
| --- | --- |
| Graduated Count | Number of learners who have met the graduation requirement |
| Graduation Rate | % of Paid Enrolled who graduated |
| Grad Target | `progress / target` — progress is On-Track count (active cohorts) or Graduated count (grace/closed). Target = `ceil(activated × graduation_target_pct / 100)`. Green if meeting target, yellow if behind. |

**Week on Week**

A delta indicator on each funnel count comparing to the immediately previous upload (not a fixed 7-day window). Displayed as ▲N, ▼N, or — (flat).

### 9.7 Sorting

| Sort | Default |
|---|---|
| Alphabetical by Programme Name | Default |
| At-Risk Count Descending | Toggleable |

### 9.8 Snapshot Export

PNG or PDF snapshot of the portfolio — aggregated headline numbers only, not a per-cohort table. Designed to fit on a single Google Slide. See [Section 11](#11-snapshot-export).

---

## 10. Dashboard — Cohort View

### 10.1 Purpose

The cohort view is the learner-level detail view. The LCE arrives here by clicking a cohort name from the portfolio view. It shows every paid-enrolled learner in the cohort with their current funnel stage, submission data, at-risk status, and LMS activity.

### 10.2 Page Structure

```
Cohort Summary Bar (condensed portfolio row for this cohort)
─────────────────────────────────────────────────────────────
Filter Bar
─────────────────────────────────────────────────────────────
Learner Table (one row per paid-enrolled learner)
─────────────────────────────────────────────────────────────
Bulk Action Bar (export selected)
```

### 10.3 Cohort Summary Bar

A condensed version of the portfolio row displayed at the top of the cohort view. Retains context when the LCE drills in from the portfolio.

Contains:
- Programme name
- Cohort name
- State badge (Active / Grace Period / Closed)
- Current week
- Graduation requirement % and graduation target % (shown in subtitle)
- Funnel counts and rates (same columns as portfolio row)
- Graduation count and rate
- **Graduation target card** — shows `On-Track / target` (active cohorts) or `Graduated / target` (grace/closed). Target is `ceil(activated × graduation_target_pct / 100)`. Green border if on track, yellow if below; displays shortfall count when behind.
- Week-on-week delta indicators per funnel count

### 10.4 Filter Bar

| Filter | Options |
| --- | --- |
| Funnel Stage | Enrolled (unpaid) / Activated / On-Track / At-Risk / Inactive. "Activated" shows all learners who have met the activation threshold — includes On-Track, At-Risk, and the narrow activated sub-stage. On-Track and At-Risk can be used to drill down within that group. "Enrolled (unpaid)" shows learners who appear in the dataset but have not yet paid. |
| At-Risk Flag | Any specific flag (1–5) |
| Juggler Status | Juggler / Non-Juggler |
| Last Login | Date range filter on days since last login |

> Additional filter dimensions will be added as needed in future iterations.

### 10.5 Learner Table

Each row is one paid-enrolled learner. Default sort is alphabetical by name. Secondary sort by at-risk flag count descending is toggleable.

**Identity**

| Column | Detail |
|---|---|
| Name | Clickable — navigates to Learner Profile (V1.2) |
| Email | Displayed as plain text |
| Phone | Displayed as plain text. All four programme formats provide a phone column. |

**Funnel**

| Column | Detail |
|---|---|
| Stage | Activated / On-Track / At-Risk / Inactive |
| Progress % | LMS score as reported in the weekly CSV |

**Submissions**

| Column | Detail |
|---|---|
| Submissions | Total submissions across all types (`total_submissions_all` — milestones + quizzes + assignments). Falls back to `total_submitted` (milestones only) if `total_submissions_all` is null. |
| Expected | Expected milestone submission count at current checkpoint |
| Gap | Expected minus Submitted. Negative = behind. |

**LMS Activity**

| Column | Detail |
|---|---|
| Days Since Last Login | Derived from last login date. Displayed as a number e.g. "12 days". Sortable. Only shown for programmes where the LMS activity column is configured. |

**At-Risk**

| Column | Detail |
|---|---|
| Flag Indicator | Displays the single highest-priority active flag per learner. Uses increasing shades of red — light red for low severity, deep red for highest severity. If no flags are active the indicator is neutral. Hovering shows which specific flag is active and its recommended action. |

**Graduation** *(Grace Period and Closed cohorts only)*

| Column | Detail |
|---|---|
| Graduation Status | Graduated / Not Yet / Pending |

**Juggler**

| Column | Detail |
|---|---|
| Juggler Indicator | A simple badge if the learner is paid-enrolled in other active programmes. |

### 10.6 Bulk Actions

LCE can select individual rows or all rows (respecting current filter). Available actions:

- Export selected learners to Excel
- Export selected learners to CSV

### 10.7 Snapshot Export

PNG or PDF snapshot of the cohort summary bar — not the full learner table. Designed to fit on a single Google Slide. See [Section 11](#11-snapshot-export).

---

## 11. Snapshot Export

### 11.1 Purpose

A presentation-ready summary that an LCE can drop directly onto a Google Slide without any formatting work. Available from both the portfolio view and the cohort view.

### 11.2 Format

- Output: PNG or PDF
- Layout: Single slide-sized frame
- Rendering: WeasyPrint from a Bootstrap HTML template
- Design: Deferred to build phase

### 11.3 Portfolio Snapshot Content

Aggregated headline numbers across all cohorts visible under the current filter:

- Total Paid Enrolled
- Total Activated (count + rate)
- Total Active (count + rate)
- Total On-Track (count + rate)
- Total At-Risk (count + rate)
- Total Graduated (count + rate)
- Juggler count
- Week-on-week deltas per metric
- Filter context (e.g. "2026 — All Programmes")
- Timestamp and upload reference the numbers are based on

### 11.4 Cohort Snapshot Content

Full cohort summary for one cohort:

- Programme name + cohort name
- State badge
- Current week
- Funnel counts and rates with week-on-week deltas
- Graduation count and rate
- At-risk flag breakdown (count per flag)
- Timestamp and upload reference

---

## 12. Data Model Overview

The following models form the core schema. Field-level detail is defined during build. This overview reflects the full range of fields observed across all four programme CSV formats (VA, AiCE, Cybersecurity, Graphic Design).

### Design principles

- **Email is always the learner anchor.** All CSV formats provide email. All other identifiers are supplementary.
- **Programme-specific fields are nullable.** Fields that only exist in some CSV formats are stored as nullable/blank. The ingestion engine sets what it finds; missing fields are left null.
- **Assignment activity is parsed into ItemSubmission rows.** Comma-separated assignment name strings are matched against CohortItem records using rapidfuzz (threshold 85). Unmatched items are logged and skipped.
- **Exit and graduation are separate dimensions.** A learner can exit a cohort (exit_status) and independently have a graduation outcome (graduation_status). These do not collapse into one field.

---

### 12.1 Learner

*Identity record — one row per unique person, keyed on email.*

| Field | Type | Notes |
|---|---|---|
| `email` | EmailField unique | Primary identifier. Always lowercase, no whitespace. |
| `first_name` | CharField | Split from Full name if source has single field |
| `last_name` | CharField | |
| `gender` | CharField nullable | VA/AiCE only |
| `birth_date` | DateField nullable | VA/AiCE only. Age and age range are derived at query time. |
| `country_of_residence` | CharField | All programmes |
| `country_of_origin` | CharField | All programmes |
| `locality` | CharField | City/area — VA/AiCE only |
| `phone` | CharField | All programmes (different column names) |
| `linkedin_url` | CharField nullable | Merged from primary + new profile columns. First non-null non-n/a value used. |
| `created_at` / `updated_at` | DateTimeField | |

---

### 12.2 Programme

*A learning track. Configuration is set once before first upload.*

| Field | Type | Notes |
|---|---|---|
| `name` | CharField unique | |
| `code` | CharField unique | Short code e.g. VAC, AICE, CS, GD |
| `tracks_quizzes` | BooleanField | Whether quiz submissions count in funnel logic |
| `payment_column` | CharField | CSV column for payment status |
| `payment_type` | CharField | `boolean` / `date` / `numeric` / `text` |
| `payment_value` | CharField | The value that means paid |
| `lms_activity_column` | CharField | CSV column for last LMS login date. Blank = not tracked. |
| `flag5_threshold_days` | PositiveIntegerField | Default 14 |
| `activation_week` | PositiveIntegerField | Default 1. Week by which first qualifying submission must occur. |
| `activation_submission_type` | CharField | `milestone` / `quiz` / `any` / `both`. Default `milestone`. |
| `column_mapping` | JSONField | Saved after first upload verification. Maps system fields → CSV column headers. |

---

### 12.3 Cohort

*A time-bound run of a programme. State is always manually managed.*

| Field | Type | Notes |
|---|---|---|
| `programme` | FK → Programme | |
| `name` | CharField | e.g. VAC16 |
| `code` | CharField | Short code |
| `state` | CharField | `active` / `grace` / `closed`. No auto-transitions. |
| `start_date` / `end_date` | DateField | |
| `graduation_requirement_pct` | DecimalField | Minimum LMS score / progress % a learner must reach to graduate. |
| `graduation_target_pct` | PositiveSmallIntegerField | `60` or `80`. The cohort's graduation target expressed as a percentage of activated learners. Used to compute the target headcount shown on portfolio and cohort views. Default: 80. |
| `location_program_cohort` | CharField blank | Campus/location code e.g. `ACC-AiCE-0126`. All learners in a cohort share this value — stored here, not on Enrollment. |
| `is_self_paced` | BooleanField | Reserved for future use. |

---

### 12.4 CohortWeekExpectation

*Explicit expected cumulative submission counts per week. Used by Flags 2 and 3.*

| Field | Type | Notes |
|---|---|---|
| `cohort` | FK → Cohort | |
| `week_number` | PositiveIntegerField | |
| `expected_submissions` | PositiveIntegerField | Cumulative milestone count expected by end of this week |

---

### 12.5 CohortItem

*A named milestone or quiz within a cohort. ItemSubmission rows reference these.*

| Field | Type | Notes |
|---|---|---|
| `cohort` | FK → Cohort | |
| `name` | CharField | Matched against CSV assignment name strings using rapidfuzz |
| `item_type` | CharField | `milestone` / `quiz` |
| `released_week` | PositiveIntegerField | |
| `order` | PositiveIntegerField | Display order |

---

### 12.6 Enrollment

*Links a Learner to a Cohort. Holds computed funnel stage, at-risk flags, and all per-enrolment status fields.*

#### Funnel and flags

| Field | Type | Notes |
|---|---|---|
| `learner` | FK → Learner | |
| `cohort` | FK → Cohort | |
| `is_paid` | BooleanField | Derived from payment column on ingestion. The funnel gate. |
| `funnel_stage` | CharField | `enrolled` / `activated` / `on_track` / `at_risk` / `inactive` |
| `flag_no_submissions` | BooleanField | Flag 1 |
| `flag_two_weeks_behind` | BooleanField | Flag 2 |
| `flag_dropped_from_track` | BooleanField | Flag 3 |
| `flag_below_grad_req` | BooleanField | Flag 4 |
| `flag_lms_inactivity` | BooleanField | Flag 5 |

#### Graduation and exit — separate dimensions

| Field | Type | Notes |
|---|---|---|
| `graduation_status` | CharField nullable | `graduated` / `not_yet` / `pending` / `failed`. `failed` = did not meet graduation requirements (maps from `Class enrollment status = FAILED` in CS/GD). Null = outcome not yet determined. |
| `exit_status` | CharField nullable | `dropped_off` / `dismissed` / `withdrawn` / `deferred`. Null = still enrolled. |
| `exit_reason` | TextField nullable | |
| `exit_date` | DateField nullable | |
| `deferred_to_cohort` | FK → Cohort nullable | Set when `exit_status = deferred` |

#### Programme-specific fields

| Field | Type | Notes |
|---|---|---|
| `cohort_group` | CharField blank | Sub-group within a cohort e.g. `3-C-GD` — GD only |
| `is_nitda_learner` | BooleanField | AiCE only |

---

### 12.7 WeeklySnapshot

*One row per learner per cohort per week. Created on each ingestion. Holds cumulative counts.*

#### Core

| Field | Type | Notes |
|---|---|---|
| `enrollment` | FK → Enrollment | |
| `week_number` | PositiveIntegerField | |
| `snapshot_date` | DateField | Date of ingestion |
| `progress_pct` | DecimalField nullable | LMS Overall score |

#### Submission counts — all nullable (not all programmes provide breakdown)

| Field | Type | Notes |
|---|---|---|
| `total_submissions_all` | PositiveIntegerField nullable | Cross-programme anchor — all submission types combined |
| `total_submitted` | PositiveIntegerField nullable | Milestone submissions |
| `total_milestones` | PositiveIntegerField nullable | Milestone pool size |
| `total_milestones_passed` | PositiveIntegerField nullable | |
| `total_quiz_submissions` | PositiveIntegerField nullable | Renamed from `total_quizzes` |
| `total_quizzes` | PositiveIntegerField nullable | Quiz pool size |
| `total_quizzes_passed` | PositiveIntegerField nullable | |
| `total_assignments` | PositiveIntegerField nullable | Total assignment pool (milestones + quizzes) |
| `total_assignments_passed` | PositiveIntegerField nullable | |

#### LMS engagement signals

| Field | Type | Notes |
|---|---|---|
| `last_login_date` | DateField nullable | eHub last login — used by Flag 5 |
| `has_logged_into_lms` | BooleanField | AiCE/CS/GD |
| `has_enrolled_in_lms` | BooleanField | CS/GD |
| `has_logged_into_ehub` | BooleanField | All programmes |
| `ehub_active_last_two_days` | BooleanField | All programmes |
| `circle_shown_up` | BooleanField | All programmes |
| `circle_active_last_two_days` | BooleanField | All programmes |
| `is_engaged_in_circle` | BooleanField | Merged from `Is engaged in Circle` (VA/AiCE) and `Is active circle member` (CS/GD) |

#### Outcome and context signals

| Field | Type | Notes |
|---|---|---|
| `is_graduated_lms` | BooleanField | Raw LMS graduation flag (all programmes, different column names) |
| `is_met_graduation_criteria` | BooleanField nullable | AiCE only |

---

### 12.8 ItemSubmission

*One row per learner per CohortItem. Parsed from assignment name strings in the CSV on each ingestion. Item names matched against CohortItem.name using rapidfuzz (threshold 85). Unmatched items are logged.*

| Field | Type | Notes |
|---|---|---|
| `enrollment` | FK → Enrollment | |
| `cohort_item` | FK → CohortItem | |
| `status` | CharField | `accessed` / `submitted` / `passed` / `failed` / `missing` |

Status is derived from which assignment lists the item appears in:

- In `passed` list → `passed`
- In `submitted` but not `passed` → `submitted`
- In `accessed` but not `submitted` → `accessed`
- In `missing` only → `missing`
- In `failed` list → `failed`

CS/GD provide only `accessed` and `missing` lists. VA/AiCE provide all five.

---

### 12.9 IngestionLog

| Field | Type | Notes |
|---|---|---|
| `cohort` | FK → Cohort | |
| `uploaded_by` | FK → User | |
| `uploaded_at` | DateTimeField | |
| `week_number` | PositiveIntegerField | |
| `filename` | CharField | |
| `file_content` | BinaryField | Stored for background worker access |
| `status` | CharField | `pending` / `processing` / `complete` / `failed` |
| `rows_processed` | PositiveIntegerField | |
| `rows_skipped` | PositiveIntegerField | |
| `skipped_rows` | JSONField | Bad row details — downloadable |
| `was_overwrite` | BooleanField | Snapshot already existed for this cohort+week |
| `was_late_correction` | BooleanField | Cohort was Closed at time of upload |

---

## 13. Technical Stack

| Layer | Technology | Rationale |
|---|---|---|
| Backend framework | Django | ORM maps cleanly to data model. Admin panel covers programme configuration UI. Mature ecosystem for background jobs, exports, and auth. |
| Database | PostgreSQL | Relational structure required. Django Q2 uses it as the job broker — no Redis needed. |
| Background jobs | Python `threading.Thread` | Jobs run immediately in a background thread within the web server process on upload confirmation. No separate worker process required. The follow-on funnel computation step runs in a second thread after ingestion completes. Django Q2 is retained as a dependency but no longer used for job dispatch. |
| Frontend interactivity | HTMX | Partial page updates for filters, tab switching, ingestion progress. Stays within Django's server-rendered world. |
| Client-side state | Alpine.js | Lightweight JavaScript for interactions HTMX handles awkwardly — bulk row selection, column mapping UI, dropdown state. |
| CSS framework | Bootstrap 5 | Prebuilt components (tables, badges, modals, tabs, forms) map directly to the designed views. Fast development velocity for a solo developer building a data-dense internal tool. |
| Form rendering | django-crispy-forms + crispy-bootstrap5 | Renders Django forms in Bootstrap 5 automatically. Covers upload form, programme configuration, column mapping verification. |
| Table rendering | django-tables2 | Renders querysets as Bootstrap-styled sortable tables. Used for portfolio cohort table and cohort learner table. |
| Filtering | django-filter | Pairs with django-tables2 for filter bar on portfolio and cohort views. |
| Snapshot export | WeasyPrint | Renders Bootstrap HTML templates to PDF. PNG conversion from PDF output. Simple deployment on Railway/Render. |
| Deployment | Railway or Render | Managed PostgreSQL + Django hosting. Django Q2 worker runs as a separate process. |
| Fuzzy matching | rapidfuzz | Fast, accurate fuzzy string matching for column header detection. |

---

## 14. Non-Functional Requirements

### 14.1 Performance
- CSV ingestion must not block the UI. Processing runs as a background job. LCE receives immediate feedback on upload submission.
- All database writes during ingestion use atomic batch operations (Django `bulk_create` / `bulk_update` with `update_or_create`). A single transaction failure rolls back the entire ingestion.
- Dashboard views must load within 3 seconds under normal data volumes (12+ programmes, multiple cohorts each, hundreds of learners per cohort).

### 14.2 Reliability
- Ingestion failures must be caught, logged, and surfaced to the LCE without data corruption.
- Snapshot overwrites are non-destructive in the audit sense — the ingestion log always records what happened and who triggered it.

### 14.3 Usability
- No technical knowledge required to operate the platform after setup.
- Column mapping verification must be intuitive — the LCE sees exactly what the system detected and can correct it without understanding the underlying schema.
- The platform is web-based and mobile-responsive.

### 14.4 Security
- Authentication required for all views.
- Role-based access control — LCE, PM, and Leadership roles have appropriate view permissions.
- No learner personal data exposed beyond what is necessary for each view.

### 14.5 Extensibility
- The ingestion module is structured to support automated Google Drive pull in V1.2 without rearchitecting.
- The cohort model includes an `is_self_paced` flag ready for self-paced programme support in a future version.
- Each future module (call centre, learner portal, juggler page, programme view) can be added as a new Django app without structural changes to the existing system.

---

## 15. Use Cases (Gherkin)

### UC-01: Upload Weekly CSV

```gherkin
Feature: Weekly CSV ingestion

  Scenario: Successful upload with auto-detected programme and cohort
    Given the LCE is logged in
    And they navigate to the Upload page
    When they upload a CSV file named "GraphicDesign_VAC16_Week3.csv"
    Then the system reads the filename and file metadata
    And pre-fills the programme as "Graphic Design" and cohort as "VAC16"
    And displays the column mapping verification screen
    And shows all detected field mappings with confidence indicators
    When the LCE confirms the mapping
    Then the system queues a background ingestion job
    And immediately returns a "Processing" status to the UI
    And when processing completes, displays an ingestion summary showing rows processed, rows skipped, and whether it was an overwrite

  Scenario: Upload where programme cannot be detected
    Given the LCE uploads a CSV with an unrecognised filename and no file metadata
    Then the system displays the verification screen
    And shows empty dropdowns for programme and cohort
    And the LCE must manually select the programme and cohort before confirming

  Scenario: Upload with bad rows
    Given the LCE uploads a CSV where 3 rows have malformed email addresses
    When ingestion completes
    Then the 3 bad rows are silently skipped
    And the ingestion summary shows "3 rows skipped"
    And provides a downloadable list of the skipped rows

  Scenario: Upload overwrites an existing snapshot
    Given a snapshot already exists for Cohort VAC16 Week 3
    When the LCE uploads a corrective CSV for the same cohort and week
    Then the existing snapshot is overwritten
    And the ingestion log records the overwrite with the LCE's identity and timestamp

  Scenario: Corrective upload on a closed cohort
    Given cohort VAC15 is in Closed state
    When the LCE uploads a corrective CSV for VAC15
    Then the system accepts the upload
    And processes it normally
    And the ingestion log records the late correction flag as TRUE
```

### UC-02: Funnel Stage Computation

```gherkin
Feature: Automated funnel stage computation

  Scenario: Learner is paid-enrolled but has no milestone submissions
    Given a learner appears in the CSV
    And their payment column resolves to the paid value
    And they have zero milestone submissions
    Then their funnel stage is set to "Enrolled"
    And they are classified as Inactive

  Scenario: Learner submits their first milestone
    Given a learner is currently Enrolled and Inactive
    And the latest CSV shows they have submitted at least one milestone
    Then their funnel stage is updated to "Activated"

  Scenario: Activated learner meets quiz and test requirements for all previous weeks
    Given a learner is Activated
    And their quiz and test submissions meet all checkpoint requirements up to the current week
    Then their funnel stage is set to "On-Track"

  Scenario: Activated learner has not met checkpoint requirements
    Given a learner is Activated
    And one or more at-risk flags are triggered
    Then their funnel stage is set to "At-Risk"
    And they remain classified as Active

  Scenario: Unpaid learner in the CSV
    Given a learner appears in the CSV
    And their payment column does not resolve to the paid value
    Then the learner is excluded from all funnel computation
    And excluded from all at-risk flag evaluation
    And excluded from all dashboard metrics
```

### UC-03: At-Risk Flag Evaluation

```gherkin
Feature: At-risk flag computation

  Scenario: Flag 1 — No submissions after activation
    Given a learner is Activated
    And their total submission count (all types) has not increased since their activation week
    Then Flag 1 is set to TRUE on their enrollment record

  Scenario: Flag 2 — Two weeks behind on submissions
    Given a learner is Activated
    And their submission count is below the expectation for two checkpoint periods
    Then Flag 2 is set to TRUE

  Scenario: Flag 3 — Dropped from On-Track
    Given a learner was previously On-Track in a prior snapshot
    And their current submission count is 3 or more weeks behind the current checkpoint expectation
    Then Flag 3 is set to TRUE

  Scenario: Flag 4 — Below graduation requirement
    Given a learner is Activated
    And their progress percentage is below the cohort graduation requirement at the current checkpoint
    Then Flag 4 is set to TRUE

  Scenario: Flag 5 — Long LMS inactivity
    Given a learner is Activated
    And the programme has an LMS activity column configured
    And the learner's last login date is more than the programme's configured inactivity threshold days ago
    Then Flag 5 is set to TRUE

  Scenario: Flag 5 — Programme has no LMS activity column configured
    Given a learner is Activated
    And the programme does not have an LMS activity column configured
    Then Flag 5 is marked as not applicable
    And does not fire or produce an error

  Scenario: Multiple flags active simultaneously
    Given a learner has triggered Flag 1 and Flag 3
    Then both flags are stored independently on the enrollment record
    And the cohort view displays only Flag 1 (highest priority)
    And the learner profile displays all active flags
```

### UC-04: Portfolio View

```gherkin
Feature: Portfolio dashboard

  Scenario: LCE lands on the portfolio view
    Given the LCE is logged in
    When they navigate to the dashboard
    Then they see the portfolio view as the default landing page
    And the Active tab is selected by default
    And the summary bar shows total paid enrolled, activated, active, at-risk, and juggler count across all visible cohorts
    And the cohort table shows one row per Active or Grace Period cohort
    And rows are sorted alphabetically by programme name

  Scenario: LCE filters by year
    Given the LCE is on the portfolio view
    When they select "2026" from the year filter
    Then the cohort table updates to show only cohorts where the start date OR end date falls within 2026
    And the summary bar updates to reflect the filtered cohorts

  Scenario: LCE sorts by at-risk count
    Given the portfolio view is showing multiple cohort rows
    When the LCE clicks the at-risk sort toggle
    Then the cohort table re-sorts with the highest at-risk count cohort at the top

  Scenario: LCE views closed cohorts
    Given the LCE is on the portfolio view
    When they click the Closed tab
    Then the cohort table shows only cohorts in Closed state
    And the summary bar updates accordingly

  Scenario: LCE exports a portfolio snapshot
    Given the LCE is on the portfolio view
    When they click the snapshot export button
    Then the system generates a PNG or PDF
    And it contains aggregated headline metrics for all currently visible cohorts
    And it is formatted to fit on a single Google Slide
```

### UC-05: Cohort View

```gherkin
Feature: Cohort dashboard

  Scenario: LCE drills into a cohort
    Given the LCE is on the portfolio view
    When they click a cohort name
    Then they are taken to the cohort view for that cohort
    And the cohort summary bar shows the condensed funnel metrics for this cohort
    And the learner table shows all paid-enrolled learners sorted alphabetically

  Scenario: LCE filters learner table by at-risk flag
    Given the LCE is on the cohort view
    When they select "Flag 1 — No Submissions" from the flag filter
    Then the learner table updates to show only learners with Flag 1 active

  Scenario: LCE exports a filtered learner list
    Given the LCE has filtered the cohort view to show only at-risk learners
    When they select all filtered rows and click Export to Excel
    Then the system generates an Excel file containing only the selected learners and their data

  Scenario: LCE exports a cohort snapshot
    Given the LCE is on the cohort view
    When they click the snapshot export button
    Then the system generates a PNG or PDF of the cohort summary bar
    And it is formatted to fit on a single Google Slide

  Scenario: Cohort in Grace Period shows graduation status
    Given the LCE navigates to a cohort in Grace Period state
    Then the learner table includes a Graduation Status column
    And each learner row shows Graduated, Not Yet, or Pending
```

---

## 16. Decisions Log

A record of key product decisions made during scoping. Reference this when Claude Code sessions require context on why something was built a certain way.

| Decision | Rationale |
|---|---|
| Paid enrollment is the funnel gate, not raw enrollment | Unpaid learners inflate metrics and are not operationally relevant. Payment status is configured per programme because column names vary across CSVs. |
| Activation is programme-configurable (`activation_week` + `activation_submission_type`) | What counts as activation and when it must happen varies across programmes — some require a milestone by week 1, some a quiz and milestone by week 2, others either type. A single global rule would produce false inactive flags for cohorts that don't release milestones until week 2. Configuring this per programme keeps Flag 1 meaningful across all programme structures. |
| Flag 1 compares `total_submissions_all`, not the activation-type-specific count | Originally Flag 1 compared milestone-only count for VA/AiCE, which caused false positives: a learner with 7 total submissions (quizzes + assignments) but no new milestones would be flagged for "no submissions." Flag 1's intent is complete disengagement — if a learner has done any new work of any type, they should not be flagged. Using `total_submissions_all` on both sides of the comparison (activation week vs current week) ensures the flag only fires when a learner has truly stopped submitting anything. |
| Active is a parent category containing On-Track and At-Risk | A learner does not become inactive by being at-risk. At-risk is a flag on an active learner, not a separate funnel stage. |
| Flag 5 replaced "Partially Stalled" | The original Flag 5 definition overlapped too heavily with Flags 2 and 4. Long LMS inactivity is a genuinely distinct signal that informs a different intervention. |
| Flag 5 threshold is programme-configurable | Different programmes have different expected login cadences. A global default of 14 days is set but overridable. |
| Cohort state transitions are manual | Auto-closing cohorts based on end date would create incorrect state changes when graduation is delayed. The LCE team needs control over when a cohort moves to Grace Period and Closed. |
| Closed cohorts accept corrective uploads | Graduation data may need correction after a cohort closes. The audit log records late corrections explicitly. |
| Snapshots overwrite rather than stack | Re-uploading for the same week should correct the data, not create duplicate entries. The ingestion log preserves the audit trail. |
| Background threads over Django Q2 | Django Q2 requires a separate `qcluster` worker process. For an internal tool with a single server, a Python `threading.Thread` started on upload submission achieves the same non-blocking behaviour without an additional process to manage. Jobs start immediately; no polling queue is needed. |
| On-track threshold uses previous week's expectation | Uploading data in the same week a new milestone is released would immediately drop learners off On-Track if the threshold used the current week's count. Using `expected_at_week(week - 1)` gives a one-week grace window, reflecting operational reality. |
| Expected submission count caps at total milestones released | The `_expected_at_week` function returns the count of `CohortItem` milestone records released on or before that week. In catch-up weeks where no new milestones are released, the expected count stays flat rather than inflating linearly. Linear fallback only applies to cohorts with no `CohortItem` records configured at all. |
| `total_submitted = 0` is treated as zero, not missing | Python's `or` operator treats `0` as falsy. Using `is not None` ensures that an explicit zero milestone count (learner genuinely has not submitted) does not fall back to `total_submissions_all` (which includes quizzes). The `total_submissions_all` fallback is reserved for CS/GD programmes where the milestone column is `NULL` because it does not exist in their CSV format. |
| Dashboard "Submissions" column shows `total_submissions_all`, not `total_submitted` | `total_submitted` only counts milestones (max 4 for AiCE C17), while `total_submissions_all` reflects every submission type (milestones + quizzes + assignments, up to 10 for AiCE C17). LCEs found the milestone-only count confusing because it never matched the numbers they saw in the LMS. Showing the combined count aligns the dashboard with learner-visible progress. |
| Graduation target is a cohort-level configuration (60% or 80%) | The graduation target is the proportion of activated learners LCE aims to graduate, not the per-learner pass mark (that is `graduation_requirement_pct`). Two values are offered (60 / 80) to match LCE's existing target framework. |
| New cohorts must have `CohortItem` records configured | Without `CohortItem` records, `_expected_at_week` falls back to a linear count equal to the week number, which drastically overestimates expectations and flags all learners as behind. When creating a new cohort for an existing programme, copy `CohortItem` and `CohortWeekExpectation` records from a prior cohort of the same programme. |
| Model fields stripped to only those with a defined use case | Speculative fields (raw LMS strings, redundant booleans, CRM IDs) were removed from WeeklySnapshot (`lms_category`, `employment_status`, `next_payment_due_date`), Enrollment (`lms_enrollment_activated`, `lms_status`, `other_programs_enrolled`), and Learner (`lms_identifier`, `hubspot_id`). Fields kept if they have a CSV source across multiple programmes or a documented role in a future module (hub attendance, call centre, reporting, learner profile). |
| Exit fields (`exit_reason`, `exit_date`) wired to CSV | VA CSVs contain `"Droped-off reason"` and `"Droped-off_date"` columns (including the CSV typo) that were previously ignored. The ingestion engine now reads these into `exit_reason` and `exit_date` on Enrollment. A `"Learner dropped-off"` boolean is also now detected and sets `exit_status = dropped_off` for VA/AiCE learners. |
| Phone number displayed in cohort view | `phone` was stored on the Learner model and imported from all four programme CSVs but never shown on the dashboard. LCEs need it for outreach. Added as a column in the cohort learner table. |
| Bootstrap 5 over Tailwind | Internal ops tool prioritises development velocity over design customisation. Bootstrap's prebuilt components (tables, badges, modals, tabs) map directly to the designed views. |
| WeasyPrint over Playwright for snapshot export | Bootstrap HTML renders cleanly through WeasyPrint to PDF. Simpler deployment with no headless browser overhead. |
| Week-on-week delta compares to immediately previous upload | Not a fixed 7-day window. If uploads are irregular, the delta reflects actual change since the last known data point. |
| Date filter uses inclusive logic (start OR end date) | A cohort starting in October 2024 and ending March 2025 should appear in both 2024 and 2025 filters. Filtering by start date only would make cohorts disappear from a year's outlook prematurely. |
| Juggler = paid-enrolled in more than one active programme | An unpaid enrollment in a second programme does not make a learner a juggler operationally. |
| Self-paced programmes deferred | Insufficient operational clarity on pod structure and tracking rhythm. Schema includes `is_self_paced` flag for future implementation without structural changes. |

---

## 17. Out of Scope — V1

The following are explicitly excluded from V1 and will not be built:

- Learner profile view
- Programme view
- Juggler page (juggler count metric in summary bar is included; the dedicated page is not)
- Call centre module (call queue, call logging, initiative tracking, bulk Excel import for call teams)
- Learner portal (learner-facing progress view)
- Reporting module (portfolio health report, week-on-week progression report, revenue proxy report)
- Payments processing or payment gateway integration
- Direct LMS integration (data enters via CSV only)
- Mobile application (platform is web-based and mobile-responsive)
- Automated Google Drive ingestion trigger
- Self-paced programme support
- Buddy system

---

## 18. Deferred to V1.2

The following are designed for but deferred to V1.2:

| Feature | Notes |
|---|---|
| Learner Profile | Cross-programme view, full snapshot history, call log history |
| Programme View | All cohorts within a programme with aggregate metrics |
| Juggler Page | Filterable table of all jugglers with per-programme funnel stages and at-risk flags |
| Call Centre Module | Call queue, in-app logging, follow-up queue, bulk Excel import, initiative impact report |
| Learner Portal | Learner authentication, personal progress view, cohort aggregate view |
| Reporting Module | Portfolio health report, revenue proxy report, week-on-week progression |
| Google Drive Ingestion | Automated scheduled pull — ingestion module provisioned for this in V1 |
| Self-Paced Programmes | Pod-based tracking — schema provisioned with `is_self_paced` flag |

---

## 19. Build Sequence — V1

### Step 1 — Project & Infrastructure Setup
- Django project initialised with PostgreSQL on Railway or Render
- Django Q2 installed and configured with PostgreSQL broker
- Bootstrap 5, HTMX, Alpine.js integrated
- django-crispy-forms, crispy-bootstrap5, django-tables2, django-filter installed
- WeasyPrint installed and smoke-tested
- Environment config and secrets management
- Basic authentication

### Step 2 — Core Data Models
- Learner, Programme, Cohort, CohortCalendar, CohortItem
- Enrollment, WeeklySnapshot, ItemSubmission
- IngestionLog
- Django migrations and admin registration

### Step 3 — Programme & Cohort Configuration UI
- Programme creation and editing (name, quiz tracking, payment config, LMS activity config, Flag 5 threshold)
- Cohort creation and editing (start date, end date, graduation %, state management)
- CohortItem management (milestone and quiz list with release weeks)
- CohortCalendar setup

### Step 4 — Ingestion Engine
- CSV parser with rapidfuzz column header matching
- Auto-detect programme and cohort from filename and file metadata
- Verification screen (programme, cohort, column mapping, payment field, LMS activity field)
- Inline new programme / cohort creation from verification screen
- Mapping persistence per programme
- Background thread dispatch — starts immediately on confirmation, no separate worker needed
- Atomic batch upsert with transaction rollback on failure
- Bad row skipping and collection
- Post-ingestion summary view
- Upload history page (`/ingestion/jobs/`) with HTMX live refresh
- Navbar badge showing count of active jobs
- "Run now" fallback for stuck pending jobs
- Revert upload — deletes snapshots for a cohort+week and resets enrollment stages
- Audit log entry on every ingestion (including overwrite and late correction flags)

### Step 5 — Funnel & At-Risk Computation Engine
- Paid enrollment filter as funnel gate
- Stage computation: Enrolled → Activated → Active (On-Track / At-Risk)
- All 5 at-risk flags evaluated independently per enrollment
- Grace Period logic: full flags + graduation status computation
- Computation triggered automatically as a follow-on job after every ingestion

### Step 6 — Portfolio View Dashboard
- Summary bar with total metrics and juggler count
- Filter bar (year, date range, programme)
- Active tab (Active + Grace Period cohorts)
- Closed tab
- Cohort table with all columns as specified in Section 9
- Alphabetical sort (default) + at-risk count sort (toggle)
- HTMX-powered filter and sort updates (no full page reload)
- Portfolio snapshot export (PNG/PDF via WeasyPrint)

### Step 7 — Cohort View Dashboard
- Cohort summary bar
- Filter bar (funnel stage, at-risk flag, juggler status, last login range)
- Learner table with all columns as specified in Section 10
- Bulk row selection with Alpine.js
- Export to Excel and CSV
- Cohort snapshot export (PNG/PDF via WeasyPrint)

---

## 20. Glossary

| Term | Definition |
|---|---|
| Cohort | A time-bound group of learners running a specific programme (e.g. VAC16). |
| CohortItem | A named milestone or quiz that counts toward a learner's LMS score for a cohort. |
| Enrollment | The record linking one learner to one cohort. One learner can have multiple enrolments. |
| Funnel Stage | The current classification of a learner's progress: Enrolled → Activated → Active → On-Track or At-Risk. |
| Grace Period | The window after a cohort's end date during which graduation is still being finalised. Uploads are still accepted. |
| Ingestion | The process of uploading a weekly CSV and having the system update all learner records automatically. |
| Juggler | A learner paid-enrolled in more than one active programme simultaneously. |
| LCE | Learner Community Executive — the team member responsible for learner engagement and retention. |
| Paid Enrollment | An enrollment where the learner's payment status column resolves to the configured paid value. The gate for entering the funnel. |
| Progress % | A learner's overall LMS score as reported in the weekly CSV export. |
| Revenue Proxy | Since direct payment data is limited, active paid enrollment is used as a proxy for paid participation. |
| Self-Paced | A programme structure where learners progress at their own speed. Deferred to a future version. |
| Snapshot | A record of a learner's engagement and submission data at a specific point in time, created on each CSV upload. |
| WeeklySnapshot | The model storing per-learner submission and progress data captured on each ingestion. |


