# LCE Learner Platform — Product Requirements Document

**Version:** 1.1
**Status:** Current
**Date:** May 2026
**Classification:** Confidential

> **Changelog — v1.2 → v1.3 (May 2026)**
> **Section 4 (Upload Process):** Full preview step added — file is now stored on disk (FileField) rather than as a database blob. Upload page shows XHR progress bar during file transfer. Preview runs asynchronously with a 3-step progress display (Reading & parsing, Detecting programmes, Counting learners). Admin reviews a programme breakdown before confirming ingestion. 7-step ingestion pipeline progress bar visible during processing.
> **Section 4 (Ingestion performance):** Learner, Enrolment, and CourseEnrolment upserts now use MariaDB `INSERT … ON DUPLICATE KEY UPDATE` — eliminates the read-before-write pattern that caused lock wait timeouts at scale. AssignmentProgress batch size increased to 2000.
> **Section 6 (Health thresholds):** Default thresholds updated to reflect realistic engagement patterns: activation 14 days, inactivity 14 days, dormancy 21 days, stuck assignment 14 days, pass rate 50%, inter-course 14 days.
> **Section 8 (Admin capabilities):** User management added — admins can create, edit, and deactivate staff accounts from within the app (Admin → Users), no Django admin required.
> **Section 18 (File upload security):** CSV files are stored in a non-web-accessible directory (`private_media/`) outside the web root and deleted from disk after ingestion completes, ensuring sensitive learner data is not retained as files.

> **Changelog — v1.1 → v1.2 (May 2026)**
> **Section 5 (Enrolment model):** Added `has_activity_data` boolean field — distinguishes eHub activity-CSV enrolments (True) from roster-only enrolment-CSV records (False). Used to split "In eHub" vs "Roster Only" counts on the home dashboard.
> **Section 11 (Home dashboard):** Added Programme Health Breakdown table — per-programme row showing Roster Only vs In eHub split, Activated/Active/At Risk/Dormant/Graduated rates, all status counts as clickable links to filtered learner list.
> **Section 11 (Analytics):** Cohort table expanded — each status cell now shows count + rate (e.g. "12 (40%)") and is a clickable link to the learner list filtered by cohort date range + health status.
> **Section 11 (Manager Report):** Weekly breakdown — week label cell and Active/At Risk cells are now clickable links to the learner list with cohort + health filters.
> **Section 14 (Pod System):** `courses_behind` capping logic updated — capped at courses NOT YET STARTED (total_courses − completed − in_progress). A learner on the last in-progress course now shows 0 courses behind instead of an inflated number. WALX excluded from the fallback total_courses count.
> **Section 13 (Concurrency):** Documented that activity CSV (IngestionJob) and enrolment CSV (EnrolmentUploadJob) can run simultaneously — they use separate job-type guards and don't block each other. Best practice: run enrolment CSV first.

> **Changelog — v1.0 → v1.1 (May 2026)**
> Added Analytics page and Manager Report page to Section 11. Updated Section 5 (Programme fields — start_date, end_date, is_prerequisite; upcoming exclusion rule; PaymentStatus.UNKNOWN). Updated Section 6 (Activated metric definition now based on first-module completion; Retained metric added; health rollup status labels aligned to implementation). Added Section 11a — Metric Definitions Reference. No structural changes to core data model or snapshot system.

---

## Table of Contents

1. Overview & Purpose
2. Design Principles
3. Users & Roles
4. Data Sources & Ingestion
5. Core Data Model
6. Health Classification
7. Snapshot System
8. Admin Capabilities
9. Information Architecture & Navigation
10. LCE Workflows
11. Key Views & What They Answer
12. Empty States, Alerts & Notifications
13. Intervention Log
14. Initiative Module — Pod System
15. Out of Scope & Future Modules
16. Open Questions & Assumptions
17. Performance, UX Efficiency & Data Management
18. Security

---

## 1. Overview & Purpose

The LCE Learner Platform is a purpose-built internal tool for Learner Community Executives and Programme Managers to track, understand, and act on learner progress across self-paced programmes. It replaces fragmented manual tracking with a single, automated, always-current picture of every learner.

The platform has one job: make sure the LCE team always knows who needs attention, why, and what has already been tried.

It does this by ingesting learner progress data from the staff portal, maintaining a structured record of every learner's activity and health across every programme they are enrolled in, and surfacing that information in a way that makes the right action obvious without requiring the team to hunt for it.

**The foundational design principle is separation of concerns.** The core platform is a clean data and tracking layer. It knows about learners, programmes, courses, modules, progress, health signals, and interventions. It does not know about pods, campaigns, or call queues. Those are initiative modules that sit on top of the core and read from it. If any initiative module is removed, the core platform is unaffected.

This means the platform is built once and operated indefinitely. Adding a new initiative — a pod system, a call centre queue, an SMS campaign tool — requires no changes to the foundation. The team's job is to act on the data, not maintain the system.

**What this platform is not.** It is not a learning management system. It does not host content, manage assignments, or control course access. It does not process payments. It does not replace the staff portal or Savanna. It reads from those systems and builds a layer of operational intelligence on top of them.

---

## 2. Design Principles

These principles govern every decision made in building and extending the platform. When two approaches are in tension, these principles are the tiebreaker.

**Learner-first identity.**
Every record in the platform anchors to the learner as a person, not to a programme or enrolment. A learner enrolled in two programmes has one profile, one health picture, and one intervention history. This ensures no learner falls through the cracks because they exist in multiple places.

**Flags over scores.**
The platform does not collapse learner health into a single score or rating. It maintains independent, named flags — one for inactivity, one for payment, one for being stuck, and so on. Each flag points to a different problem and a different intervention. A composite score hides the signal; independent flags surface it.

**Compute, don't store redundantly.**
Health classifications, pace calculations, and progress summaries are computed from raw data at ingestion time and cached for display. They are never entered manually. If the underlying data changes, the computed values update automatically on the next ingestion.

**Thresholds are configurable, not hardcoded.**
The number of days that defines inactive, the pass rate that triggers a flag, the time allowed between courses — all of these are admin-configurable per programme. What is at-risk for one programme may be normal for another. The platform encodes the logic; the team sets the parameters.

**Initiatives are isolated.**
The core platform has no knowledge of pods, call queues, or campaigns. Initiative modules read from core data and write only to their own tables. Removing an initiative module leaves the core platform fully intact. This is not a convention — it is a hard architectural boundary.

**Snapshots over live calculations.**
The platform maintains a time-series of learner state. Every upload creates a snapshot. This means the team can always answer not just where a learner is today, but where they were two weeks ago, whether they are improving or declining, and whether a specific intervention made a difference.

**Built to outlast individuals.**
The platform is documented, automated, and modular. No part of it depends on institutional knowledge held by a specific person. A new LCE team member should be able to sit down, upload a CSV, and read the dashboard without training beyond a brief orientation.

**Action-oriented UI.**
Every view exists to answer a specific question or support a specific task. The platform never asks the LCE to interpret raw data. It surfaces what needs attention, in priority order, with enough context to act immediately. Information that does not drive action is either hidden or deprioritised.

**Everything is exportable.**
Every view that displays a list or table can be exported to CSV or Excel without exception. Filters applied to the view are reflected in the export. Exports always include all columns visible in the view plus any additional fields that are useful in a spreadsheet context but would clutter the UI. This principle applies universally across every view in the platform.

**Privacy by default.**
The platform is an internal operational tool. Learner data is never exposed beyond what is necessary for the task at hand. When learner-facing views are built in future, they will expose only the individual learner's own data. No learner ever sees another learner's information.

**Performance is a feature.**
The platform must feel fast at all times regardless of data volume. Background processing, caching, pagination, and lazy loading are not optimisations to be added later — they are design requirements. A slow platform erodes trust and reduces the likelihood that the LCE team uses it consistently.

---

## 3. Users & Roles

The platform serves a small, focused set of internal users. For the initial release, all internal users have full access. Role-based access control is noted as a future consideration but is not in scope now.

**Learner Community Executive (LCE)**
The primary user of the platform. LCEs are responsible for learner engagement, retention, and progression across one or more programmes. They use the platform daily to identify learners who need attention, review individual learner histories, log interventions, and track whether their outreach is working. The platform is built around their workflow first.

**Programme Manager (PM)**
Responsible for the health and performance of one or more programmes as a whole. PMs use the platform to monitor programme-level metrics, track enrolment trends, review graduation rates, and assess whether learners are progressing at a sustainable pace. They are less focused on individual learners and more focused on aggregate patterns and signals that require structural responses.

**Admin**
Responsible for configuring and maintaining the platform. Admins create and edit programme, course, and module records, set health flag thresholds per programme, manage data uploads, and resolve ingestion errors. In the initial release, any team member can act as admin. As the team grows, this role may be restricted.

**Future roles (out of scope for initial release)**
The following roles are anticipated but not built in the initial release:

- Call Representative — works a call queue generated from the at-risk list, logs call outcomes, manages follow-ups. Delivered as part of the call centre initiative module.
- Learner — accesses a personal progress view through a learner-facing portal. Delivered as a separate future module.
- Leadership / Finance — accesses executive-level reporting on programme health and revenue proxy metrics. Delivered as a read-only reporting view in a later phase.

**A note on access in the initial release.**
All LCE team members see all programmes and all learners. There is no assignment of specific learners or programmes to specific team members at this stage. If the team grows to a size where this becomes necessary, role-based scoping can be added without structural changes to the platform.

---

## 4. Data Sources & Ingestion

The platform has one data source: a CSV export downloaded manually from the staff portal. Everything the platform knows about learners, their progress, and their status comes from this file. There is no direct integration with Savanna, eHub, or any other system in the initial release. Automation of the download process is a future consideration.

### The CSV Structure

The CSV is exported at the assignment level. Each row represents one learner's record for one specific assignment. A single learner will appear in multiple rows — one per assignment across all courses they are enrolled in. Learner-level attributes such as name, gender, country, platform access, and payment status are repeated across every row belonging to that learner.

The fields present in the CSV are grouped as follows:

**Learner identity** — email, first name, last name, gender, country of residence, region.

**Platform access** — has logged into eHub, eHub profile URL, has logged into LMS, LMS profile URL, has shown up in course.

**Cross-enrolment** — count of other programmes enrolled, names of other programmes.

**Enrolment & activation** — is enrolment activated, activation date, days since activation, first sign of life date, days since first sign of life.

**Course context** — course sequence number, course name, eHub class name, course status on LMS, is course graduated, course graduation date.

**Assignment detail** — assignment name, assignment type (milestone, test, or n/a), is assignment accessed, assignment accessed date, is assignment submitted, assignment submitted date, is assignment passed, passed on first attempt.

**Programme completion** — is programme graduated, programme graduation date, is graduated on Savanna.

**Health & payment** — learner health classification, payment status.

### Null Date Convention

All date fields default to 1970-01-01 00:00:00 when the event has not occurred. The ingestion process treats any date at this value as null. This applies to activation date, first sign of life date, course graduation date, assignment accessed date, assignment submitted date, and programme graduation date.

### Structure Extraction

The platform maintains a catalogue of programmes, courses, and assignments. This catalogue does not need to be manually configured before the first upload. On the first upload for any programme, the system extracts the full programme → course → assignment hierarchy automatically.

It does this by reading the course name, course sequence number, eHub class name, assignment name, and assignment type fields across all rows. Each unique combination of programme, course sequence number, and course name produces a course record. Each unique assignment name within a course produces an assignment record, typed as milestone, test, or other based on the assignment type field.

Subsequent uploads extend the catalogue incrementally. If a new course or assignment appears in a file that was not present in any previous upload, it is added to the catalogue automatically. Existing records are not overwritten — only new entries are added.

Admins can supplement the extracted catalogue at any time. If a course or assignment is known to exist but has not yet appeared in any CSV — because no learner has reached it yet — the admin can add it manually. This ensures the catalogue is always at least as complete as the data, and can be ahead of it when needed.

When the system encounters a course or assignment in a new upload that partially matches an existing catalogue entry — same sequence number but a slightly different name, for example — it flags the conflict for admin review rather than creating a duplicate or silently overwriting the existing record.

### Programme and Course Detection

The file may contain data for a single programme or for multiple programmes simultaneously — this is expected and handled automatically.

The system detects which programme and course each row belongs to by parsing the course name and eHub class name fields. The eHub class name carries structured information that the system decodes directly. The format follows the pattern `[PROGRAMME]-[COURSE_NUMBER]_[CLASS_IDENTIFIER]`. For example, `VA-1_C#1` decodes as: programme = VA, course sequence number = 1. `AICE-2_rolling` decodes as: programme = AICE, course sequence number = 2. The decoded course sequence number is cross-referenced against the course catalogue to confirm the match.

Admins maintain a registry of known programme prefixes and eHub class name patterns. This registry is editable from the admin interface and grows over time as new programmes are onboarded. Where a pattern is unrecognised, the system surfaces the raw value to the admin with a prompt to map it to an existing programme and course or register it as a new pattern.

Where the values decoded from the eHub class name conflict with the course name field in the same row, the system flags the discrepancy for admin review rather than resolving it silently.

### Upload Process

An LCE or admin downloads the CSV from the staff portal and uploads it through the ingestion interface. A real-time progress bar in the browser shows the file being transferred to the server. Once transferred, the file is stored temporarily on disk (not in the database) and the server immediately begins an asynchronous preview analysis. The upload interface transitions automatically to an analysis screen showing three live progress steps — reading and parsing the CSV, detecting programmes and courses, counting new and existing learners. When analysis completes, the admin reviews a programme breakdown showing which programmes and courses were found, how many rows matched, and any rows that could not be resolved. The admin then confirms or cancels. On confirmation, the full 7-phase ingestion pipeline runs in the background. The file is deleted from disk once ingestion completes.

On upload, the system performs the following steps in order:

**Validation** — the system checks that required columns are present, date formats are consistent, and no critical fields are empty. If validation fails, the upload is rejected immediately with a specific error message identifying what is wrong and on which rows. Validation is synchronous — the LCE sees the result before the file is queued.

**Programme and course detection** — the system parses each row's course name and eHub class name against the programme registry and course catalogue, assigning each row to the correct programme and course automatically. Rows that cannot be matched are flagged for admin review. The upload proceeds for all matched rows; flagged rows are held pending resolution.

**Structure extraction** — the system identifies any new courses or assignments not currently in the catalogue and adds them automatically. Conflicts with existing catalogue entries are flagged for review.

**Learner matching** — each email is matched against the learner master. If no match is found, a new learner record is created. If a match is found, learner-level attributes are updated with the latest values from the file.

**Progress upsert** — assignment-level progress records are updated if the assignment is already known for that learner, or inserted if not.

**Health flag recomputation** — health flags are recomputed for every learner touched by the upload, based on the latest data and the current threshold configuration for that programme.

**Snapshot creation** — a snapshot is created for every active learner enrolment, capturing their state at the time of upload.

**Ingestion log entry** — a log record is written capturing the uploading user, timestamp, rows processed, new learners created, learners updated, new assignments recorded, flags raised, and any warnings or non-fatal errors.

### Ingestion Log

Every upload produces a single ingestion log entry. This log is visible to admins and provides a full audit trail of every data update. The log entry includes the programme, the uploading user, the timestamp, the number of rows processed, the number of new learners created, the number of existing learners updated, the number of new assignments recorded, the number of rows flagged for review, and any warnings or non-fatal errors encountered during processing.

### Upload Frequency

There is no enforced upload schedule. The platform updates whenever a new file is uploaded. The snapshot system is designed to make irregular upload cadence visible: if two uploads are far apart, the gap will show in the trend data and the platform will surface a warning if no upload has been received for a programme within a configurable number of days.

### Future: Automated Ingestion

The staff portal may support automated or scheduled exports in future. When that becomes available, the ingestion pipeline is designed to accept programmatic uploads without changes to the processing logic. The manual upload interface remains available regardless.

---

## 5. Core Data Model

The platform is structured around the learner as the central identity. Every other record in the system relates back to a learner. This ensures that a learner enrolled in multiple programmes has one unified profile, one intervention history, and one health picture, regardless of how many rows they occupy in the source CSV.

The data model is organised into four logical groups: the learner and their identity, the programme catalogue and its structure, the learner's progress through that structure, and the operational records that support LCE work.

### Group 1: Learner Identity

**Learner**
The master record for every unique person in the system. Created on first appearance in any CSV upload and never deleted. One row per person, ever.

Fields: email (primary key), first name, last name, gender, country of residence, region, eHub profile URL, LMS profile URL, has logged into eHub, has logged into LMS, has shown up in course, other programmes count, other programme names, current overall health status, payment status, first seen date, last updated date.

Payment status lives on the learner record because it is learner-level, not programme-level. A learner has one payment status regardless of how many programmes they are enrolled in.

Overall health status is a rollup of the worst active health flag across all of the learner's active programme enrolments. It is computed on ingestion and cached here for display efficiency. The authoritative health signal always lives on the enrolment record.

### Group 2: Programme Catalogue

This group defines the structure of learning — what programmes exist, what courses they contain, and what assignments those courses are made of. This structure is extracted automatically from CSV uploads and supplemented manually by admins where needed.

**Programme**
One record per learning track. Created automatically on first detection in a CSV upload or manually by an admin.

Fields: programme ID (primary key), programme name, programme code (e.g. AICE, VA), total courses required for graduation, start date (optional — the calendar date the programme opens to learners), end date (optional — the date the programme closes), is active, is prerequisite, created date, created by.

**Upcoming programme rule.** If `start_date` is set and is in the future (relative to today's date), the programme is considered *upcoming* and is excluded from all enrolment counts, health distributions, weekly breakdowns, and analytics views. Upcoming programmes appear in admin configuration but not in any operational metric. This prevents pre-launch enrolment records from inflating reported numbers.

**Prerequisite programme rule.** If `is_prerequisite` is true, the programme is a shared foundation programme (e.g. Welcome to ALX) rather than a tracked learning track. Prerequisite programmes are excluded from all learner-facing and manager-facing views. They are retained in the data model so that learner progress through prerequisites is observable, but they are never aggregated into health or enrolment metrics.

**PaymentStatus.UNKNOWN rule.** Learners whose `payment_status` field is `UNKNOWN` (representing an unpaid or unverified payment state) are excluded from all enrolment-based metrics and counts. They are visible to admins but do not contribute to enrolled, activated, active, at-risk, graduated, or retention figures. This prevents unconfirmed enrolments from distorting operational data.

**Course**
One record per course within a programme. Courses are ordered by sequence number. Created automatically from CSV uploads or manually by admins.

Fields: course ID (primary key), programme ID (foreign key), course sequence number, course full name, course code (e.g. AICE-1, VA-2), prerequisite course ID, expected duration in days, is active, created date, created by.

The prerequisite course ID allows the system to understand that AICE-2 cannot begin until AICE-1 is complete. For the first course in a programme, this field is null unless a standalone onboarding course such as Welcome to ALX is defined as a prerequisite.

**Assignment**
One record per assignment within a course. Created automatically from CSV uploads or manually by admins.

Fields: assignment ID (primary key), course ID (foreign key), assignment name, assignment type (milestone, test, other), sequence number within course, pass threshold percentage, is required for course completion, is active, created date, created by.

Pass threshold and is required for course completion are admin-configurable per assignment. These defaults can be overridden at the programme level in the threshold configuration.

**Programme Identifier Registry**
The registry of known eHub class name patterns and course name prefixes, each mapped to a programme and course. Used by the ingestion system for automatic detection.

Fields: registry ID (primary key), raw pattern, pattern type (eHub class name, course name prefix), mapped programme ID, mapped course ID, created date, created by.

### Group 3: Learner Progress

This group tracks what each learner has done, where they are, and how they got there.

**Enrolment**
The record linking one learner to one programme. One learner can have multiple enrolment records if they are enrolled in multiple programmes. This is the primary unit of health classification.

Fields: enrolment ID (primary key), learner email (foreign key), programme ID (foreign key), enrolment date, first sign of life date, activation date, current course ID, current health status, active health flags, is programme graduated, programme graduation date, is graduated on Savanna, last updated date.

Current health status and active health flags are computed on ingestion and stored here. The flags stored on this record are the authoritative source for all health views and reports.

**Course Enrolment**
The record linking one learner to one course within a programme. Created when a learner first appears in a course in the CSV. Tracks opt-in, progression, and completion at the course level.

Fields: course enrolment ID (primary key), enrolment ID (foreign key), course ID (foreign key), opt-in date, course status (not started, in progress, completed, withdrawn), course completion date, course pass percentage, is passed, last activity date, last updated date.

Opt-in date reflects when the learner first engaged with the course, as detected from the CSV. The platform does not manage the opt-in mechanic itself in the initial release — it observes and records what Savanna reports.

**Assignment Progress**
One record per learner per assignment. This is the finest grain of data in the platform, corresponding directly to the row-level grain of the CSV.

Fields: progress ID (primary key), course enrolment ID (foreign key), assignment ID (foreign key), is accessed, accessed date, is submitted, submitted date, is passed, passed on first attempt, attempt count, last updated date.

Attempt count is derived from the number of submission records for the same learner and assignment across uploads. Passed on first attempt is set on the first submission and never changed thereafter.

### Group 4: Operational Records

This group supports the LCE team's day-to-day work. It records what actions have been taken on behalf of learners and provides the audit trail that makes intervention effectiveness measurable.

**Intervention Log**
One record per intervention per learner. An intervention is any deliberate action taken by an LCE team member in response to a learner's health signal. Interventions can be logged manually by an LCE or automatically by the system when a configured automated action fires.

Fields: intervention ID (primary key), learner email (foreign key), enrolment ID (foreign key, optional), intervention date, intervention type, intervention reason, outcome, follow-up required, follow-up date, notes, logged by, initiative ID (foreign key, optional).

The initiative ID field is the only point of contact between the core platform and initiative modules. It is a nullable foreign key. When null, the intervention is standalone. When populated, it allows initiative modules to claim interventions as part of a campaign without the core platform needing to know what a campaign is.

**Ingestion Log**
One record per CSV upload. Described fully in section 4.

**Staff User**
One record per internal team member. Used to attribute interventions, uploads, and admin actions.

Fields: staff user ID (primary key), name, email, role (LCE, PM, admin), is active, created date, last login date.

### Key Design Decisions

**The learner is the anchor, not the programme.** Deleting a programme record does not delete the learner. Learner records are permanent.

**Progress records are append-friendly.** Assignment progress records are upserted on each upload — updated if they exist, inserted if they do not. Historical states are preserved in snapshots, not in the progress records themselves.

**The catalogue grows forward, never shrinks.** Courses and assignments are never deleted from the catalogue. They are marked inactive. This preserves the integrity of historical progress records that reference them.

**Opt-in is observed, not managed.** The platform detects course opt-ins from the CSV data. It does not control or gate course access in the initial release.

**The initiative ID is a clean seam.** Initiative modules attach to the core through this single nullable field on the intervention log. No other core table has any knowledge of initiatives.

---

## 6. Health Classification

Health classification is the platform's primary mechanism for surfacing which learners need attention and why. It is computed automatically on every ingestion and stored on the enrolment record. It is never entered manually.

The classification system has two layers. The first is a set of independent, named flags — each one tracking a specific signal that points to a specific problem and a specific type of intervention. The second is a rollup label that summarises the worst active flag into a single status for display and filtering. The flags are the authoritative signal. The label is a convenience.

All thresholds that drive flag computation are admin-configurable per programme. They are not hardcoded.

### Key Metric Definitions

The following metrics appear throughout the platform, in manager reports, analytics views, and exports. Their definitions are canonical and must be applied consistently across all views.

**Enrolled** — the count of active enrolment records for a programme, excluding upcoming programmes (`start_date` > today), prerequisite programmes (`is_prerequisite = true`), and learners with `PaymentStatus.UNKNOWN`. Enrolment date attribution uses the *effective enrolment date* (see below) to correctly handle learners who enrolled before the programme's official start date.

**Effective Enrolment Date** — the date used to assign an enrolment to a reporting period (e.g. a weekly bucket). Computed as: `MAX(enrolment_date, programme.start_date)` handling nulls on both sides. This means a learner who enrolled before the programme launched is attributed to the programme's launch week, not their original enrolment date. This is a display and bucketing concept only — it does not modify the stored enrolment date.

**Activated** — a learner is considered Activated when they have passed the first module of the first course in their programme. Technically: there exists a `CourseEnrolment` record for this learner and programme where `is_passed = true` and the associated course has the minimum `sequence_number` among all courses in that programme. Note: the CSV provides an `activation_date` field which is stored on the enrolment record, but the *Activated* metric is computed live from `CourseEnrolment.is_passed` to reflect the actual first-module completion event, not the staff portal's activation timestamp.

**Retained** — a learner is Retained if they are both Activated and currently have a health status of Active, At-Risk, or Graduated. A retained learner has made meaningful first progress and is still engaged with the programme. Retained is a sub-metric of Activated.

**Active** — health status is `ACTIVE`. The learner is progressing without any flags.

**At-Risk** — health status is `AT_RISK`. One or more health flags are active (inactivity, stuck, low pass rate, payment issue, etc.) but the learner has not crossed the dormancy threshold.

**Dormant** — health status is `DORMANT`. The learner has been inactive beyond the dormancy threshold or has never activated beyond the hard never-activated threshold.

**Graduated** — the learner has completed the programme. No active flags are evaluated.

**Not Started** — the learner has no first sign of life date and no assignment activity.

**Interventions** — count of `Intervention` records logged for a learner or group of learners within the reporting period.

### What the Platform Can and Cannot Measure

The platform derives all activity signals from dated events in the CSV — assignment accessed date, assignment submitted date, first sign of life date, and course graduation date. It has no session data, no login duration, and no time-on-platform metrics. Last activity date is the most recent date across all assignment accessed and submitted dates for a learner within a programme. Days inactive is the number of days between that date and the date of the current upload.

Pass rate is computed from boolean outcomes. The CSV reports whether each assignment was passed, not a numeric score. Pass rate is therefore the ratio of passed assignments to total submitted assignments, counted across all of the learner's assignment rows for a given programme enrolment.

### The Flags

**Flag 1 — Never Activated**
The learner has a first sign of life date (or an enrolment date) but has not yet passed the first module of the first course — i.e. they are not yet *Activated* in the platform's canonical sense. This flag fires when the number of days since first sign of life exceeds the activation threshold configured for the programme. The appropriate intervention is an early welcome outreach to prompt first meaningful engagement.

**Flag 2 — Inactive**
The learner has previously accessed or submitted at least one assignment but has not done so within the inactivity threshold. Last activity date is the most recent assignment accessed or submitted date across all of the learner's active courses in the programme. The appropriate intervention depends on the duration of inactivity.

**Flag 3 — Stuck on Assignment**
The learner has accessed a specific assignment but has not submitted it within the stuck threshold. The clock starts from the assignment accessed date. The flag identifies the specific assignment the learner is stuck on. The appropriate intervention is academic support targeted at the specific assignment.

**Flag 4 — Low Pass Rate**
The ratio of passed assignments to total submitted assignments has fallen below the pass rate threshold. Pass and fail outcomes are read directly from the is assignment passed boolean field. The appropriate intervention is academic support focused on understanding where the learner is going wrong.

**Flag 5 — Stalled Between Courses**
The learner has completed a course but has not begun the next course within the inter-course threshold. The clock starts from the course completion date. The flag identifies which course was completed and which has not yet been started. The appropriate intervention is a prompt to continue.

**Flag 6 — Payment Issue**
The learner's payment status is anything other than compliant. The flag stores the specific payment status value — due soon, grace period, or overdue. This flag is not threshold-driven. It fires whenever the payment status field is not compliant.

### Flag Independence

Every flag is stored independently on the enrolment record. A learner can have multiple flags active simultaneously. Each active flag is displayed separately with its own label, context, and recommended action type. The platform never collapses multiple flags into a single message.

### Health Status Rollup

The health status label is computed from the active flags and stored on both the enrolment record and the learner master record. It is a display convenience, not an authoritative signal.

The rollup follows this priority order (first matching rule wins):

1. **Graduated** — the learner has completed the programme. No further flags are evaluated.
2. **Dormant** — the learner has been inactive beyond the dormancy threshold, or has never activated beyond the hard never-activated threshold.
3. **At Risk** — one or more flags are active and the learner has not crossed the dormant threshold.
4. **Active** — no flags are active and the learner has first sign of life or assignment activity.
5. **Not Started** — the learner has no first sign of life date and no assignment activity.

These five labels map directly to the `HealthStatus` enum: `GRADUATED`, `DORMANT`, `AT_RISK`, `ACTIVE`, `NOT_STARTED`. They are used consistently in the UI, exports, charts, and all filter interfaces throughout the platform.

The overall health status on the learner master record reflects the worst status across all active programme enrolments.

### Threshold Configuration

Thresholds are configured per programme by an admin and stored in a threshold configuration record linked to the programme. The following thresholds are configurable:

| Threshold | Description | Default |
|---|---|---|
| Activation threshold | Days after first sign of life before never activated flag fires | **14 days** |
| Inactivity threshold | Days without activity before inactive flag fires | **14 days** |
| Dormancy threshold | Days without activity before status escalates to dormant | **21 days** |
| Stuck assignment threshold | Days after assignment access before stuck flag fires | **14 days** |
| Pass rate threshold | Minimum pass ratio before low pass rate flag fires | **50%** |
| Inter-course threshold | Days after course completion before stalled flag fires | **14 days** |
| Upload warning threshold | Days since last upload before staleness warning fires | 7 days (unchanged) |

All thresholds have system-wide defaults. Admins can override per programme at any time. Changes take effect on the next ingestion and do not retroactively alter historical snapshots. Every threshold change is logged with the admin's name and timestamp.

### Health Classification and the Initiative Layer

Health flags are the primary input to initiative modules. No initiative module computes its own health signals — they all consume what the core platform has already computed. There is one version of the truth about every learner's health, and every tool that acts on it reads from the same place.

---

## 7. Snapshot System

The snapshot system is the platform's mechanism for tracking change over time. A single CSV upload tells you where a learner is today. The snapshot system tells you where they were last week, whether they are improving or declining, and whether a specific intervention made a difference.

### What a Snapshot Is

A snapshot is a point-in-time record of a learner's state within a programme enrolment, created automatically on every upload. It captures the learner's progress, activity, and health at the moment the upload was processed. Snapshots are never updated after creation. They are an immutable record of what was true at a specific point in time.

One snapshot is created per active learner enrolment per upload. If a learner is enrolled in two programmes, two snapshots are created on each upload — one per enrolment.

### What a Snapshot Captures

Each snapshot record stores the following at the time of upload:

**Identity** — learner email, enrolment ID, programme ID, upload date, upload ID.

**Progress** — current course sequence number, number of courses completed, total assignments accessed, total assignments submitted, total assignments passed, pass rate at this point in time.

**Activity** — last activity date, days since last activity, days since first sign of life, days since activation.

**Health** — health status label, list of active flags at this moment.

**Payment** — payment status value.

### When Snapshots Are Created

A snapshot is created for every active learner enrolment each time a CSV is uploaded. There is no minimum interval between snapshots — if two uploads happen on the same day, two snapshots are created. Snapshots are only created for enrolments touched by the upload. The absence of a snapshot is informative and is surfaced in the trend view as a gap.

### How Snapshots Enable Trend Analysis

By comparing snapshots across time, the platform can compute the following:

**Week-on-week progress** — assignments submitted between the last upload and this one, derived by subtracting previous snapshot submitted count from current.

**Health trajectory** — the sequence of health status labels across snapshots, showing whether a learner is improving, declining, or stable.

**Flag history** — when each flag first fired, whether it has been resolved and re-fired, and how long it has been active.

**Intervention response** — whether a learner's activity or progress changed after an intervention was logged, by aligning intervention timestamps with snapshot dates.

**Enrolment cohort retention** — for learners who first appeared in a given week, what percentage are still active at two, four, and eight weeks.

### Rolling Enrolment and Snapshot Age

Because enrolments are rolling, snapshot comparisons must account for enrolment age. The platform always displays days since first sign of life alongside progress metrics so LCEs compare learners in context. Enrolment cohort views group learners by the week of their first snapshot.

### Snapshot Management

Snapshots are retained indefinitely by default. They are the historical record of the platform and the foundation of all trend analysis.

Admins and authorised users can delete snapshots from the frontend interface. Deletion is soft by default — the snapshot is hidden from all views and calculations but retained in the database for audit purposes. Hard deletion, which permanently removes the record, requires an additional confirmation step and is logged in the audit trail with the user's identity and timestamp.

If a threshold is changed, historical snapshots are not recomputed. They reflect what was true under the thresholds active at the time. This preserves the integrity of historical intervention evaluations.

---

## 8. Admin Capabilities

The admin layer is the configuration and maintenance surface of the platform. It is not a separate role in the initial release — any team member can perform admin actions. Admin capabilities are grouped into four areas: programme catalogue management, threshold configuration, ingestion management, and user management.

### Programme Catalogue Management

**Programme management**
Admins can create, edit, and deactivate programmes. Changing a programme code updates the identifier registry — the admin is warned before saving. Inactive programmes are hidden from LCE views but their data is fully retained.

**Course management**
Admins can add courses to a programme manually, edit course records, and mark courses as inactive. Edits to course sequence number or prerequisite course trigger a warning because they affect pace calculations and health flag logic.

**Assignment management**
Admins can add assignments to a course manually, edit assignment records including pass threshold and required for completion flag, and mark assignments as inactive. Historical progress records referencing inactive assignments are retained.

**Conflict resolution**
When the ingestion system flags a conflict — a course or assignment in a new upload that partially matches an existing catalogue entry — the admin sees it in a dedicated conflict queue. For each conflict the admin can map the incoming value to the existing record, create a new record, or dismiss it as a known variant. Dismissed variants are added to an ignore list.

**Programme identifier registry**
Admins can view, add, edit, and delete entries in the registry. Each entry maps a raw eHub class name pattern or course name prefix to a programme and course. Deleting a registry entry does not affect historical data but will cause future uploads containing that pattern to require manual mapping.

### Threshold Configuration

Thresholds are set per programme with system-wide defaults applied when no programme-specific configuration exists. The admin threshold interface displays the current value, the system default, and the date each threshold was last changed. Admins can reset any threshold to the system default at any time.

When an admin saves a threshold change, the platform displays a confirmation explaining that the change takes effect on the next ingestion and will not alter historical snapshots. All threshold changes are logged with the admin's name and timestamp.

### Ingestion Management

**Ingestion log**
The ingestion log lists every upload in reverse chronological order with drill-down into each entry including the specific rows that were flagged and the reason for each flag.

**Flagged row queue**
Rows that could not be automatically matched during ingestion are held here. Resolving a flagged row triggers reprocessing of that row's data — the learner's progress and health are updated as if the row had been correctly matched on the original upload.

**Re-ingestion**
Admins can trigger a re-ingestion of any previously uploaded file. Re-ingestion recomputes health flags and creates a new set of snapshots marked as re-ingested. It does not alter the original snapshots from the initial upload.

### User Management

Admins can create, edit, and deactivate staff user accounts directly from the LearnSync interface at `/admin/users/`. No access to the Django admin panel is required for routine account management.

**Creating a user** — name, email, password (minimum 8 characters), staff/superuser permissions. Usernames must be unique.

**Editing a user** — all fields except username are editable. Password field left blank = keep existing password.

**Deactivating a user** — toggles the active flag. The user cannot log in while inactive but all records attributed to them (uploads, interventions, admin actions) are retained and continue to be attributed to their account. Admins cannot deactivate their own account.

Only staff members can access user management. Superuser checkbox can only be set by a superuser.

---

## 9. Information Architecture & Navigation

The information architecture defines how the platform is organised from the user's perspective. The guiding principle is that the platform should always answer one question before the LCE has to ask it: **what needs my attention right now?**

### Top-Level Sections

The platform has seven top-level sections accessible from a persistent navigation sidebar.

**Home — Daily Briefing**
The default landing view. Answers: what needs my attention today? A prioritised action list derived from health flags, recent snapshots, and pending follow-ups. Not a dashboard of all available data — a focused, ordered list of what requires action.

**Learners**
The full learner registry. Search, filter, and navigate to any learner profile. Primary lookup tool for the LCE team.

**Programmes**
Programme-level views. Each programme shows enrolled learners, course completion funnels, health distributions, and enrolment trends. Primary view for PMs.

**Interventions**
The full intervention log with follow-up queue. Log new interventions and manage pending follow-ups.

**Portfolio**
Cross-programme summary view for PMs and leadership. Programme comparison, enrolment velocity, graduation pipeline, and portfolio-level flag distribution.

**Pods**
The pod initiative module. Pod health view, member lists, pace calculations, and pod assignment management. Only visible when the pod module is active.

**Admin**
Configuration and maintenance. Programme catalogue, threshold configuration, ingestion log, flagged row queue, and user management.

### Persistent Elements

**Navigation sidebar** — all seven sections, always accessible. Badge counts on Home for unreviewed urgent flags and on Interventions for follow-ups due today.

**Global search** — accessible from any screen. Finds any learner by name or email and navigates directly to their profile.

**Upload trigger** — always visible. Opens the ingestion interface as an overlay without losing current context. Returns the LCE to their previous screen after upload with a confirmation banner.

**Data freshness indicator** — shows when each programme was last updated. Flags programmes that have not been updated within their configured upload warning threshold.

### Navigation Patterns

**Top-down** — from a population view to an individual. The primary pattern for proactive monitoring.

**Direct lookup** — global search to navigate directly to a known learner. The primary pattern for reactive work.

**Follow-up driven** — open Interventions, work through pending follow-ups in order. Each links directly to the relevant learner profile with previous intervention context visible.

### Depth of Navigation

The platform has three levels of depth.

**Level 1 — Population views** — Home briefing, learner list, programme list, intervention log. Optimised for scanning and filtering.

**Level 2 — Entity views** — individual learner profile, individual programme view. Optimised for understanding context before taking action.

**Level 3 — Detail panels** — specific assignment progress, snapshot history, single intervention record. Accessed from within Level 2 views via expandable panels or focused modals.

The platform never goes deeper than three levels. If information cannot be surfaced within three clicks from Home, the information architecture is wrong.

### Filtering and State

Filters applied in any population view persist for the duration of the session. Any filtered view generates a shareable URL that another team member can open to see the same filtered state. This supports handoffs without requiring verbal explanation of filter configuration.

### Responsive Behaviour

The platform is web-based and optimised for desktop and laptop screens. It is responsive and usable on tablet. Critical read actions — viewing a learner profile, checking the daily briefing — work on mobile. Write actions — logging an intervention, uploading a CSV — are optimised for desktop.

---

## 10. LCE Workflows

This section describes the primary workflows an LCE or PM performs in the platform day to day. Each workflow is documented as a sequence of steps reflecting how the platform supports the task end to end.

### Authentication & Session Management

Every team member has a personal login. The platform does not support shared accounts. Each session is attributed to the logged-in user — every upload, intervention log entry, admin action, and follow-up is recorded against a specific person.

On login, the platform loads the Home briefing personalised to the logged-in user. Follow-up reminders show only the interventions the logged-in user logged. The full intervention log shows all team members' activity and is filterable by user.

Session activity is logged passively: login timestamp, last active timestamp, and logout timestamp for every session. This is an operational audit trail, not a surveillance tool.

If a team member leaves the organisation, their account is deactivated by an admin. All records attributed to them are retained and remain visible attributed to their name.

### Workflow 1 — Monday Morning Triage

The LCE logs in and lands on the Home briefing organised into priority tiers. They scan from top to bottom — dormant learners first, then at-risk by flag type, then payment issues, then follow-ups due today.

For each item the LCE can see the learner's name, programme, active flags, days since last activity, and the last intervention logged. They click into the learner profile, review full context, and log an intervention before moving to the next item.

The platform tracks which briefing items have been acted on within the current session. Items with a logged intervention since the last upload are visually marked as addressed.

### Workflow 2 — Reviewing an Individual Learner

The LCE uses global search to find the learner. The profile loads with urgency-ordered information: health status and active flags at the top, programme progress and activity below, snapshot trend next, and full intervention history at the bottom.

The LCE can expand any section for more detail. Logging an intervention is always accessible from the profile without navigating away.

### Workflow 3 — Uploading a CSV

The LCE clicks the upload trigger from any screen. The ingestion interface opens as an overlay. They select the file. Validation runs immediately and synchronously — if the file fails, the LCE sees a specific error before the file is queued.

If validation passes, the file is queued for background processing. The LCE is returned to their previous screen immediately. An in-app notification arrives when processing is complete with a summary of results. If rows were flagged for review, the notification prompts resolution and explains that affected learners' data has not been updated until rows are resolved.

### Workflow 4 — Logging a Follow-up Intervention

The Home briefing surfaces follow-up reminders showing the learner's name, original intervention date, type, outcome, notes, and current health status. The LCE clicks into the learner profile, reviews the current state and previous intervention, makes contact, and logs the new intervention. Completed follow-ups are removed from the briefing and recorded in the intervention log.

### Workflow 5 — Reviewing Programme Health

The PM navigates to Programmes and selects a programme. The programme view shows health distribution, course completion funnel, enrolment trend, flag distribution, and assignment difficulty table. The PM can filter by enrolment week or health status and export any filtered view. Clicking a course with high drop-off shows the learner list for that course filtered to non-progressed learners.

### Workflow 6 — Handing Off to a Colleague

The LCE navigates to Interventions, filters by their own name and follow-up required, copies the filtered view URL, and shares it with their colleague. The colleague opens the URL, sees the same filtered state, and works through the follow-ups. Interventions they log are attributed to their own account.

---

## 11. Key Views & What They Answer

Each view exists to answer a specific question. This section defines every named view, its primary question, the secondary information it surfaces, and the actions available from it.

### Home — Daily Briefing

**Primary question:** What needs my attention right now?

Organised into priority tiers rendered in urgency order. Each tier shows a count and expands to individual learners.

- **Tier 1 — Dormant learners** — days since last activity and last intervention logged.
- **Tier 2 — At-risk by flag** — grouped by flag type, showing active flags, programme, and days since last activity.
- **Tier 3 — Follow-ups due today** — original intervention type, outcome, and current health status.
- **Tier 4 — New enrolments** — learners first appearing since last upload, with programme and first sign of life status.
- **Tier 5 — No pod assigned** — learners active beyond the auto-assignment threshold with no pod. Visible only when the pod module is active.

Actions: navigate to learner profile, log intervention, dismiss item for current session.

### Learner List

**Primary question:** Which learners match a specific set of criteria?

Paginated, searchable, filterable table. Each row shows name, email, country, active programmes, overall health status, active flag count, payment status, days since last activity, and last intervention date.

Filters: programme, health status, active flag type, payment status, enrolment week, country, region, days since last activity range, intervention status.

Actions: navigate to learner profile, export filtered view to CSV or Excel, bulk log intervention for selected learners.

### Learner Profile

**Primary question:** What is the full situation for this learner and what has already been done?

**Header** — name, email, country, region, overall health status, active flag badges, payment status, days since last activity. Always visible.

**Programme enrolments** — one card per active programme. Current course, course status, progress summary, days since last activity in this programme, programme-specific active flags. Expandable to course detail.

**Course detail (expanded)** — list of all courses with status. For in-progress and completed courses, assignment list with accessed, submitted, and passed status. Stuck assignments highlighted.

**Snapshot trend** — timeline of health status and progress across all uploads. Health status colour-coded. Progress shown as a line. Intervention markers overlaid. Hover reveals full snapshot values at any point.

**Intervention history** — all interventions in reverse chronological order regardless of who logged them. Date, type, reason, outcome, notes, follow-up status, logged by. New intervention logged directly from this section.

Actions: log intervention, navigate to programme view, export learner data to CSV or Excel.

### Programme View

**Primary question:** How is this programme performing and where are learners getting stuck?

**Health distribution** — count and percentage in each health status. Filterable by enrolment week.

**Course completion funnel** — for each course: learners reached, learners completed, drop-off rate. High drop-off courses highlighted. Clicking a course shows non-progressed learner list.

**Enrolment trend** — new enrolments by week as a bar chart. Retention table showing each enrolment week cohort at two, four, and eight weeks.

**Flag distribution** — count of learners with each flag type active. Clicking a flag type filters the learner list.

**Assignment difficulty** — assignments ordered by pass rate ascending. Lowest pass rates surfaced as potential content difficulty signals.

Actions: filter by enrolment week or health status, export to CSV or Excel, navigate to individual learner profiles.

### Portfolio View

**Primary question:** How is the overall learner portfolio performing across all programmes?

**Portfolio health summary** — total active learners across all programmes by health status with week-on-week change indicators.

**Programme comparison** — all active programmes side by side: total enrolled, active, at-risk, dormant, graduated, activation rate, days since last upload. High at-risk rates and stale data highlighted.

**Enrolment velocity** — total new enrolments across all programmes by week as a trend line.

**Graduation pipeline** — count of learners on track to graduate within 30, 60, and 90 days across all programmes.

**Flag distribution across portfolio** — count of each flag type active across all programmes. Surfaces disproportionate flag prevalence as a portfolio-level signal.

**Intervention activity** — interventions logged in the last 7 and 30 days by type and outcome.

Actions: navigate to individual programme views, export portfolio summary to CSV or Excel.

### Interventions View

**Primary question:** What has been done, by whom, and what still needs follow-up?

Paginated, filterable log of all interventions. Each row shows learner name, programme, date, type, reason, outcome, follow-up status, and logged by.

**Follow-up queue** — persistent sub-view at the top showing interventions with a follow-up date of today or earlier not yet resolved.

Filters: logged by, learner, programme, intervention type, outcome, follow-up required, follow-up date range, date range.

Actions: log new intervention, mark follow-up as resolved, navigate to learner profile, export to CSV or Excel.

### Analytics Page

**Primary question:** How are learners progressing across programmes over any time window I choose?

The Analytics page is a self-service exploration tool for LCEs and PMs. It provides aggregate totals and programme-level breakdowns, with filter controls that scope all metrics simultaneously without reloading the full page (using HTMX partial swaps).

**Filter controls** — programme (multi-select, defaults to all active non-prerequisite programmes where start_date ≤ today), date range (enrolment date window, defaults to all time). Changing any filter instantly refreshes all sections below.

**Aggregate cards** — six headline numbers reflecting the currently filtered population:

| Card | Definition |
|---|---|
| Total Enrolled | Enrolments matching the current filter (upcoming and PaymentStatus.UNKNOWN excluded) |
| Activated | Learners who have passed the first module of the first course |
| Active | Learners with health status = ACTIVE |
| At Risk | Learners with health status = AT_RISK |
| Graduated | Learners with health status = GRADUATED |
| Retained | Learners who are both Activated and have health status in Active / At Risk / Graduated |

**Programme breakdown table** — one row per programme showing: programme name, code, enrolled, activated, active, at-risk, graduated, retained, and activation rate (activated ÷ enrolled as a percentage). Rows are ordered by enrolled count descending.

**Health distribution chart** — a Chart.js doughnut or bar chart visualising the proportion of learners in each health status across the filtered population.

Actions: adjust filters to resegment the population; download filtered data.

---

### Manager Report

**Primary question:** What is the overall programme portfolio performance, and what happened week by week over the last 13 weeks?

The Manager Report is a comprehensive read-only reporting view designed for programme managers and leadership. It is not a daily monitoring tool — it is a structured summary of the current state and recent trend of the portfolio. It also exports to Excel (9 sheets) and PDF (with embedded charts).

**Report sections:**

**1 — Summary cards** — six headline numbers for the total active portfolio: Enrolled, Activated, Active, At Risk, Graduated, Retained. Same definitions as Analytics. Upcoming and PaymentStatus.UNKNOWN enrolments are excluded.

**2 — Health status distribution** — count and percentage of learners in each of the five health statuses. Includes a bar chart in both the HTML and PDF versions.

**3 — By programme table** — one row per programme showing enrolled, activated, active, at-risk, graduated, retained. Rows sorted by enrolled count descending. Includes a grouped bar chart (Enrolled / Activated / Graduated per programme) in PDF.

**4 — Weekly activity (last 13 weeks)** — a 7-column table showing what happened each week, with a 13-week line chart beneath it (HTML and PDF both):

| Column | Semantics |
|---|---|
| Week | Calendar range: Mon–Sun |
| Enrolled | Enrolments (by effective date) that fell in this week |
| Activated | First-module completions that occurred in this week |
| Active (teal) | *Current* health status of learners whose effective enrolment date was in this week |
| At Risk (red) | *Current* health status of learners whose effective enrolment date was in this week |
| Graduated | Programme graduations that occurred in this week |
| Interventions | Interventions logged in this week |

**Column semantics note:** Enrolled, Activated, Graduated, and Interventions are event counts — things that happened during that specific week. Active and At Risk reflect the *current today* health status of learners from that week's enrolment cohort. This means early weeks may show non-zero Enrolled but zero Active/At Risk/Graduated if all those learners have since changed status (graduated or become dormant). This is expected and is explained inline on the page.

**5 — Weekly activity by programme** — collapsible sections (one per active programme) each containing the same 7-column table scoped to that programme only. Uses native HTML `<details>`/`<summary>` elements — no JavaScript required.

**Effective date attribution rule for the weekly breakdown:** If a learner enrolled before the programme's `start_date`, their enrolment is attributed to the programme's start week, not their original enrolment date. This prevents early sign-ups from distorting pre-launch week counts.

**Downloads available:**

*Excel (.xlsx) — 9 sheets:*

| Sheet | Contents |
|---|---|
| 1 Summary | Six headline metrics |
| 2 By Programme | One row per programme with all metrics |
| 3 Learner Detail | One row per enrolment with full learner and health data |
| 4 Payment Status | All learners grouped by payment status |
| 5 At-Risk Flags | All currently at-risk learners with active flag detail |
| 6 Recent Interventions | Interventions from the last 30 days |
| 7 Metric Definitions | Plain-language definitions of all reported metrics |
| 8 Weekly Breakdown | 13-week activity table (9 columns including week dates) |
| 9 Weekly by Programme | Programme × week activity table |

*PDF (.pdf) — single document with:*
- Report metadata (programme, date generated)
- Summary metrics table
- Health status distribution table + horizontal bar chart (one colour per status)
- By programme table + grouped vertical bar chart (Enrolled / Activated / Graduated per programme)
- 13-week activity table + multi-series line chart (Enrolled / Activated / Active / At Risk / Graduated / Interventions)
- Metric definitions appendix

Charts in the PDF are generated server-side using ReportLab's `reportlab.graphics` library (HorizontalBarChart, VerticalBarChart, HorizontalLineChart). They do not depend on a browser rendering engine.

---

### Intervention Impact Report

**Primary question:** Did this intervention or set of interventions make a measurable difference?

Available from the Interventions view for any filtered set of interventions. For each learner in the filtered set the report shows:

**Before** — health status, active flags, days since last activity, and pass rate at the snapshot immediately preceding the intervention date.

**After 7 days** — the same metrics at the snapshot closest to 7 days post-intervention.

**After 14 days** — the same metrics at 14 days post-intervention.

**After 30 days** — the same metrics at 30 days post-intervention.

**Comparison group** — learners with the same active flags at the same time who were not contacted in the same period. The same before and after metrics shown for the comparison group so the LCE can evaluate whether the intervention produced a measurable effect above baseline.

**Summary** — percentage of contacted learners who improved health status, percentage who re-engaged, percentage who did not respond, compared against the comparison group.

The report is exportable to CSV or Excel. It is generated on demand and is not stored — it is computed from the intervention log and snapshot history at the time it is requested.

### Pod Views

Described fully in Section 14.

### Admin Views

**Ingestion log** — full upload history with drill-down. Flagged row queue with resolution interface. Re-ingestion trigger.

**Programme catalogue** — all programmes, courses, and assignments with edit capability. Conflict queue for unresolved catalogue conflicts.

**Threshold configuration** — per-programme threshold settings with current value, default, and change history.

**Programme identifier registry** — all known patterns with mappings. Add, edit, delete.

**User management** — all staff users with account status, last login, and role. Create, edit, deactivate.

---

## 12. Empty States, Alerts & Notifications

Empty states and alerts are a core part of the experience. Every empty state and every alert is an opportunity to tell the LCE exactly what is happening and what to do about it. No blank screens. No silent failures.

### Empty States

**Home briefing — no items in any tier**
Positive confirmation that all learners are currently active and no follow-ups are pending, alongside the timestamp of the last upload so the LCE knows the data is current.

**Learner list — no results for current filter**
Displays the active filters clearly and offers a one-click option to clear all filters. Names the filters that produced no results so the LCE can adjust intelligently.

**Learner profile — no interventions logged**
Displays a prompt to log the first intervention with a direct action button.

**Learner profile — no snapshot history**
Explains that trend data will appear after the next upload that includes this learner.

**Programme view — no enrolments**
Displays configuration guidance — check that the programme identifier registry entry is correct and that a CSV containing this programme has been uploaded.

**Snapshot trend — gap in upload history**
Gaps shown explicitly on the trend chart as a shaded region labelled with the number of days between uploads.

### System Alerts

**Stale data warning** — shown when a programme has not received an upload within its configured upload warning threshold. Banner on Home briefing, badge on data freshness indicator. Names the programme, shows days since last upload, includes a direct link to the upload trigger.

**Flagged rows pending** — shown when rows from a recent upload are awaiting admin resolution. Badge on Admin in the sidebar, notice on Home briefing. Shows count of affected learners and explains their data has not been updated.

**Ingestion error** — persistent banner when an upload fails validation entirely. Describes the error and links to the ingestion log entry.

**Upload complete** — in-app notification when background processing completes. Summary of results with a link to the ingestion log entry. If rows were flagged, the notification prompts resolution.

**Threshold change applied** — confirmation banner after an admin saves a threshold change. Confirms which threshold changed, the new value, and that it takes effect on the next ingestion. Auto-dismisses after 10 seconds.

### Health Flag Alerts

Each health flag alert includes the following to make it immediately actionable:

- **Who** — learner name and programme.
- **What** — the specific flag in plain language. Not "Flag 2" but "Inactive for 9 days in AICE."
- **Since when** — the date the flag first fired and how many days it has been active.
- **Context** — last activity recorded and last intervention logged.
- **Suggested action** — a plain-language prompt based on the flag type.

Alerts are never redundant. A learner with three active flags appears as one item with three flags listed — not three separate items. The LCE never sees the same learner listed multiple times in the same tier.

### Notification Design Principles

Alerts are surfaced in-platform only in the initial release. Push notifications to email or SMS are a future consideration. The alert infrastructure is designed to support them when they are added.

---

## 13. Intervention Log

The intervention log is the platform's memory of every deliberate action taken on behalf of a learner. It is the foundation of accountability, continuity, and initiative effectiveness measurement.

### What an Intervention Is

An intervention is any deliberate contact or action taken by an LCE team member in response to a learner's situation. This includes calls, emails, SMS messages, eHub messages, one-on-one meetings, and automated system actions. Passive monitoring — viewing a learner's profile — is not an intervention.

### Intervention Record Structure

Each intervention record stores:

- **Learner email** — the learner the intervention was directed at.
- **Enrolment ID** — optional. Links to a specific programme enrolment if relevant.
- **Intervention date** — when the intervention took place, not when it was logged.
- **Logged date** — when the record was created in the platform.
- **Logged by** — the staff user who created the record, set automatically from session. Cannot be edited after saving.
- **Intervention type** — call, email, SMS, eHub message, meeting, automated, other.
- **Intervention reason** — maps to an active health flag type or free-text other.
- **Outcome** — re-engaged, no response, promised action, learner withdrew, not applicable, other. Required before saving.
- **Follow-up required** — boolean. If true, a follow-up date is required.
- **Follow-up date** — required when follow-up required is true.
- **Notes** — free text. Optional but encouraged.
- **Initiative ID** — nullable foreign key. Set automatically by initiative modules. Never set manually.

### Logging an Intervention

Interventions can be logged from the learner profile, the Home briefing, and the Interventions section. The logging interface pre-populates available context — learner email, enrolment, and intervention reason when triggered from a flag alert.

Saving an intervention immediately updates the learner's profile and marks the corresponding briefing item as addressed for the current session.

The intervention log is read-only after saving. Records cannot be edited or deleted. If a record was logged in error, a correction note can be added as a follow-up intervention record. This preserves audit trail integrity.

### Automated Interventions

When an initiative module triggers an automated action, it logs intervention records automatically for each learner contacted. These records appear in the log with logged by set to the system user and intervention type set to automated. The LCE always has a complete picture of every contact a learner has received.

### Follow-up Management

Follow-ups are a property of intervention records, not a separate system. When follow-up required is true and a follow-up date is set, the record surfaces in the Home briefing on that date and in the follow-up queue in the Interventions section.

Acting on a follow-up means logging a new intervention record. The original record is not modified. The chain of contact is fully visible in the learner's intervention history. The follow-up item is removed from the queue when a new intervention is logged against the same learner after the follow-up date.

Follow-ups surface on the Home briefing of the team member who logged the original intervention. They are visible to all team members in the full Interventions section.

### Intervention Impact Report

The intervention impact report is described in full in Section 11. It is generated on demand from the Interventions view for any filtered set of interventions, comparing before-and-after snapshots for contacted learners against a comparison group of non-contacted learners with the same flags. It answers the question the team always wants to ask: did our outreach actually work?

### Initiative Attachment

The initiative ID field is the single point of contact between the intervention log and the initiative layer. Initiative modules set this field when logging or claiming interventions. The core platform stores it and returns it in exports. It never reads or interprets it.

---

## 14. Initiative Module — Pod System

The pod system is the first initiative module built on top of the core platform. It is entirely isolated from the core data layer. It reads learner progress and health data from the core and writes only to its own tables. If the pod module is disabled or removed, no core data is affected and no core functionality is broken.

The pod system answers one question the core platform does not: given that a learner has committed to finishing by a specific date, are they on track to do so?

### What a Pod Is

A pod is a group of learners who have committed to completing a specific programme by the same target month. Pods are defined by programme and target month — not by when learners joined, not by which courses they are currently on. Two learners in the June AICE pod may be on completely different courses. What they share is a deadline.

Pods are per programme. A learner enrolled in two programmes can be in a June pod for one and a December pod for the other.

### Pod Tables

The pod module maintains its own tables referencing core platform records by primary key only.

**Pod**
Fields: pod ID, programme ID (references core), pod name, target completion month, target completion date (last day of target month), pod status (active, completed, archived), created date.

**Pod Assignment**
One record per learner per programme representing current pod membership. A learner can have at most one active pod assignment per programme at any time.

Fields: assignment ID, learner email (references core), programme ID (references core), pod ID, assignment date, assignment method (self-selected, auto-assigned, admin-assigned), is current assignment, previous pod ID, pod switch date, pod switch reason, switch logged by.

The previous pod ID preserves the most recent switch history. Switches are also logged in the core intervention log for full history.

### Pod Lifecycle

**Pod creation**
Pods are created automatically when the first learner is assigned to a programme and target month combination that does not yet have a pod record. Admins can also create pods manually in advance.

**Pod selection by learner**
In the initial release, learners select their pod via a Google Form. The form data is imported into the pod assignment table by an admin via a CSV upload specific to the pod module. Assignment method is set to self-selected. A built-in pod selection form within the learner portal is a planned future enhancement.

**Auto-assignment**
When a learner has been active for longer than the auto-assignment threshold and has no pod assignment, the system projects a completion date and assigns them to the appropriate pod automatically.

Auto-assignment calculation:
1. Current pace = courses completed ÷ days since first sign of life
2. Days needed = remaining courses ÷ current pace
3. Buffered projection = days needed × (1 + buffer percentage)
4. Assign to nearest future pod that accommodates the projected date

Auto-assigned learners are flagged visibly with an auto-assigned indicator in all pod views.

**Admin assignment**
Admins and LCEs can assign or reassign any learner to any pod from the learner profile. The interface shows the learner's current pace, projected completion date, and available pods. A reason is required when overriding a self-selected assignment.

**Pod switching**
When a learner needs to move pods, an admin updates the assignment from the learner profile. The switch is recorded with a reason and logged as an intervention in the core intervention log. The previous pod ID is stored on the assignment record. Bulk pod switches can be triggered from the pod health view for learners who are significantly behind pace.

### Pace Calculation

Pace is calculated per learner per programme on every ingestion and stored on the pod assignment record.

**Clock start** — first sign of life date.

**Current pace** — courses completed ÷ days since first sign of life. Expressed as courses per day.

**Required pace** — courses remaining ÷ days remaining until pod target date. Expressed as courses per day.

**Pace status:**
- On track: current pace ≥ required pace
- Behind: current pace below required pace by less than the behind threshold
- Significantly behind: current pace below required pace by more than the behind threshold
- Ahead: current pace meaningfully exceeds required pace
- Completed: learner has graduated the programme

**Courses behind** — how many courses the learner should have completed by now based on required pace, minus actual courses completed. Positive = behind, negative = ahead.

**Projected completion date** — remaining courses ÷ current pace, added to the upload date.

### Learners Without a Pod

Learners with no pod assignment are tracked but excluded from pod health calculations. They appear in a dedicated no pod assigned view within the pod module, filterable by programme, enrolment week, and health status.

Graduated learners are retained in their original pod assignment, excluded from pace calculations, and shown as completed. Early graduates do not require manual cleanup.

### Pod Health View

All active pods for a programme shown side by side, each displaying: pod name and target date, total members, count and percentage in each pace status, average courses behind, days until target date, and a pod health indicator.

**Pod health indicator:**
- Green: configurable percentage of members on track or ahead
- Yellow: below that threshold but not critically so
- Red: significant proportion significantly behind with target date approaching

Clicking into a pod shows the full member list with individual pace status, courses behind, projected completion date, last activity date, and last intervention. Actions: log intervention for individual members, trigger bulk pod switch for significantly behind members, export to CSV or Excel.

### Interaction with Core Health Flags

Pod pace status is not a core health flag. It is a pod module signal displayed on the learner profile and in pod views but does not affect the core health status label or trigger core health flag alerts.

A learner can be behind pace in their pod but have no core health flags — they are active, submitting, passing, and paying. Pod pace is a goal-tracking signal, not a health signal. The pod module surfaces its own behind-pace alerts in the Home briefing as a distinct tier, visible only when the pod module is active.

---

## 15. Out of Scope & Future Modules

### Out of Scope — Initial Release

**Direct LMS integration** — data enters through CSV uploads only. Automated ingestion is a future consideration.

**Learner-facing portal** — learners do not have access to the platform in the initial release. The data model supports it without schema changes.

**Call centre module** — a dedicated call queue and logging interface for call representatives is planned as an initiative module. Calls are logged manually through the standard intervention log in the initial release.

**Push notifications** — alerts are surfaced in-platform only. The alert infrastructure is designed to support push notifications when added.

**eHub integration** — eHub engagement metrics are not available in the initial release. A new data source can be added to the ingestion pipeline without structural changes if an API becomes available.

**Role-based access control** — all authenticated users have full access in the initial release. The role field is designed to support access restriction in a future release.

**Payments integration** — payment status is read from the CSV only. The platform does not process payments or integrate with payment gateways.

**Mobile application** — the platform is web-based and responsive. A dedicated mobile application is not planned.

### Future Modules

**Learner portal** — learner-facing interface for viewing personal progress, selecting pods, and accessing programme resources. Separate authentication from the internal staff system.

**Call centre module** — dedicated interface for call representatives with managed call queue, in-app call logging, follow-up management, bulk import for external call teams, and initiative impact reporting.

**Pod selection form** — built-in replacement for the Google Form. Integrated with the learner portal.

**Buddy system** — peer accountability pairing with engagement tracking between paired learners.

**Announcements** — LCE-to-learner communications within the learner portal. Programme-wide or pod-specific messages.

**Resource repository** — programme and course resource hub accessible from the learner portal.

**SMS and email campaign builder** — structured campaign creation for bulk outreach. All sends logged as automated interventions in the core intervention log.

**Automated push notifications** — triggered alerts to LCE team members via email or SMS when high-severity flags fire.

**Predictive analytics** — risk scoring model using snapshot history and flag patterns to surface early dropout risk before it is visible in current data.

---

## 16. Open Questions & Assumptions

### Open Questions

**Opt-in mechanic** — when a learner completes a course, what does opting into the next course look like? Does it happen in Savanna and reflect in the CSV, or is it facilitated by the LCE team? Currently assumed: opt-in is observed from the CSV, not managed by the platform.

**Module structure variability** — is the assignment structure within a course consistent across all courses in a programme, or does it vary? Currently assumed: structure varies by course and the system makes no assumptions about counts.

**Auto-assignment buffer** — what is the right buffer percentage for projected completion dates when auto-assigning pods? Currently assumed: 20%, to be validated against real learner pace data after the first month.

**Pod selection Google Form fields** — what fields does the current form collect beyond email, programme, and target month? Additional fields should be evaluated for inclusion in the pod assignment record before migration.

**Welcome to ALX handling** — is the Welcome to ALX course counted toward pod pace or is it a prerequisite excluded from calculations? Currently assumed: prerequisite, excluded from pod pace calculations.

**Re-ingestion behaviour** — when an admin re-ingests a historical file, new snapshots are created alongside originals. Original snapshots are retained and marked as superseded with a reference to the re-ingestion event.

**Multi-programme pace display** — two independent pace indicators on the learner profile, one per programme enrolment.

### Assumptions

**Email is the stable unique identifier** — the platform uses email as the primary key for learner records. If a learner changes their email address, they will appear as a new learner. A process for merging duplicate records should be considered if this becomes a problem.

**CSV format is consistent per programme** — column structure and naming conventions are assumed consistent for a given programme across uploads. Format changes require updates to the programme identifier registry and column mappings.

**First sign of life date is reliable** — used as the baseline for all pace calculations. If this date is unreliable in the staff portal, pace calculations will be inaccurate.

**Payment status reflects current state** — a lag between payment and staff portal reflection may cause the payment issue flag to fire incorrectly for compliant learners.

**The catalogue grows forward** — courses and assignments are not removed from programmes once learners have started them. The deactivation mechanism handles retirements gracefully but course changes should be communicated to the admin before they appear in the CSV.

**Pods are the only initiative module in the initial release** — the initiative isolation architecture is validated by building one module. Subsequent modules follow the same pattern.

---

## 17. Performance, UX Efficiency & Data Management

Performance is not an optimisation to be added after the platform is built — it is a design requirement. A slow or unresponsive platform erodes trust and reduces consistent usage by the LCE team. This section defines the performance and UX efficiency requirements that apply across the entire platform.

### Asynchronous Upload Processing

CSV uploads are processed asynchronously. The upload is received and validated synchronously — the LCE sees validation results immediately. If validation passes, the file is queued for background processing and the LCE is returned to their previous screen without waiting.

Processing happens in the background. The platform displays a non-blocking progress indicator accessible from the data freshness indicator or the ingestion log. An in-app notification is delivered when processing is complete with a summary of results. If processing produces errors, the notification describes them and links directly to the ingestion log entry.

Large files — uploads covering many programmes or many learners — are processed in batches internally. The batch size is configurable at the system level. Batching prevents any single upload from monopolising system resources or causing timeouts.

### Pagination and Lazy Loading

All list and table views are paginated. No view loads an unbounded number of records at once. Default page size is configurable. The LCE can adjust page size within defined limits.

Data within complex views — such as the learner profile's assignment detail or snapshot trend — is loaded lazily. The profile header and summary sections load immediately. Detailed sections load as the LCE expands them. This ensures the most important information is always available instantly regardless of data volume.

Charts and visualisations render progressively. A skeleton placeholder is shown while data loads. The LCE is never presented with a blank panel or an indefinite spinner.

### Caching

Computed values — health status labels, flag rollups, pace calculations, pass rates — are computed at ingestion time and stored. Views read cached values, not live calculations. This means dashboard and profile views load at database read speed regardless of the complexity of the underlying computation.

Programme-level aggregates — health distributions, course completion funnels, enrolment trends — are pre-computed and cached on each ingestion. They are not recalculated on every page load.

Cache invalidation is ingestion-driven. Cached values are refreshed when a new upload is processed. Between uploads, all views reflect the state of the last upload consistently.

### Visual Design Principles

The platform is designed to be read and understood in seconds, not minutes. This requires a visual-first approach to information display.

**Health status** is always represented as a colour-coded indicator — a consistent colour system applied across every view. Green for active, amber for at-risk, red for dormant, grey for not yet started, and a distinct colour for graduated. These colours are applied consistently to badges, table rows, chart segments, and trend lines throughout the platform. The LCE should be able to scan a list of 50 learners and understand the health distribution without reading a single number.

**Progress** is shown as a combination of charts and numbers, never as raw numbers alone. Course completion is shown as a progress bar or funnel. Snapshot trends are shown as sparklines. Enrolment cohort retention is shown as a curve. Tables are used for detail, not for summary.

**Flags** are shown as labelled badges, not as text descriptions in table cells. Multiple flags on a single learner are shown as stacked or grouped badges. Flag types have consistent iconography and colour across the platform.

**Trends** are always directional. Week-on-week changes are shown with an arrow and a delta value — not just the current number. A learner with 60% pass rate trending up is in a different situation from one trending down.

### Bulk Actions

The platform supports bulk actions on filtered views. LCEs can select multiple learners from any list view and perform the following bulk actions:

- Log an intervention for all selected learners simultaneously with a shared note and individual records created per learner.
- Export selected learners to CSV or Excel.
- Assign selected learners to a pod (pod module only).

Bulk actions are performed asynchronously for large selections. The LCE is notified when the bulk action is complete.

### Frontend Data Management

Users can perform the following data management actions from the frontend without requiring admin access to a backend system:

- Delete snapshots from the snapshot trend view on a learner profile. Deletion is soft by default — the snapshot is hidden from all views and calculations but retained in the database. Hard deletion requires an additional confirmation step and is logged in the audit trail.
- Clear resolved follow-up items from the follow-up queue.
- Dismiss briefing items from the Home view for the current session.

Admins can perform the following additional actions from the Admin section:

- Bulk delete snapshots for a programme or date range.
- Purge the flagged row queue for resolved items.
- Clear the ingestion log entries beyond a configured retention period.

### Responsiveness Standards

- Page load time for any view: under 2 seconds on a standard broadband connection.
- Profile load time (header and summary): under 1 second.
- Filtered list response time: under 1 second for up to 10,000 learner records.
- Upload validation response: under 3 seconds for files up to 50,000 rows.
- Background processing notification: delivered within 60 seconds of upload completion for standard file sizes.

These are target standards, not guarantees. They should be used as acceptance criteria during the build and monitored in production.

---

## 18. Security

The platform handles personal learner data and is operated by an authenticated internal team. Security requirements apply at every layer — authentication, data handling, input validation, API design, and infrastructure.

### Authentication & Session Security

Every user accesses the platform through a personal login with a unique email and password. Shared accounts are not permitted. Passwords are hashed using a modern cryptographic algorithm. Plaintext passwords are never stored or logged.

Sessions are managed server-side with a secure, signed session token. Session tokens are transmitted over HTTPS only and are never exposed in URLs or logs. Sessions expire after a configurable period of inactivity. Users are automatically logged out when their session expires. Re-authentication is required after expiry.

Failed login attempts are rate-limited. After a configurable number of consecutive failed attempts from the same IP address, further attempts are temporarily blocked. This limit and the block duration are admin-configurable.

Account deactivation takes effect immediately. A deactivated user's active sessions are invalidated at the point of deactivation. They cannot log in again until the account is reactivated by an admin.

Multi-factor authentication is noted as a future security enhancement. The authentication infrastructure is designed to support it without architectural changes.

### Input Validation & Injection Prevention

Every input surface in the platform is validated and sanitised before processing. This applies to form fields, URL parameters, CSV file contents, and API request bodies.

**SQL injection** — all database queries use parameterised statements. Raw SQL is never constructed by concatenating user-supplied input. The ORM layer enforces this at the framework level.

**CSV injection** — CSV file contents are treated as untrusted input. Fields that could be interpreted as formulae by spreadsheet software — values beginning with =, +, -, or @ — are sanitised before processing and before inclusion in any export. The ingestion pipeline does not evaluate any field value as code.

**Cross-site scripting (XSS)** — all user-supplied content rendered in the UI is escaped. The frontend framework enforces output encoding by default. Any exception to this must be explicitly justified and reviewed.

**Cross-site request forgery (CSRF)** — all state-changing requests require a valid CSRF token. The token is tied to the authenticated session and validated server-side on every request.

**File upload security** — uploaded CSV files are stored in a non-web-accessible directory (`private_media/`) outside the web root and deleted from disk after ingestion completes, ensuring sensitive learner data is not retained as files. Uploaded files are validated for type, size, and structure before processing. The ingestion pipeline does not execute any code contained in uploaded files.

### API & Endpoint Security

All API endpoints require a valid authenticated session. Unauthenticated requests to any endpoint return a 401 response. There are no public endpoints in the initial release.

All endpoints validate that the authenticated user has the right to perform the requested action on the requested resource. In the initial release this is a simple authenticated check — all authenticated users can access all resources. The authorisation layer is designed to support role-based restrictions without endpoint restructuring.

API error responses never expose internal system details — stack traces, database error messages, or internal identifiers are logged server-side but never returned to the client. All error responses use a consistent, minimal format.

Rate limiting is applied to all endpoints. Authentication endpoints have a stricter rate limit than operational endpoints. Rate limit thresholds are configurable at the system level.

All API communication is over HTTPS. HTTP requests are redirected to HTTPS. TLS version and cipher configuration follow current security best practice.

### Data Protection

All data is encrypted in transit over HTTPS. All data is encrypted at rest using the hosting provider's managed encryption for database storage.

Sensitive fields — email addresses, profile URLs, personal identifiers — are never written to application logs. Logs capture events and errors, not personal data.

The platform does not store passwords in recoverable form. Password reset is handled via a time-limited, single-use token sent to the user's registered email address.

Data exports — CSV and Excel files generated by the platform — contain personal learner data. Export actions are logged in the audit trail with the user identity and timestamp. Export files are generated on demand and not stored on the server after delivery.

### Audit Trail

The platform maintains an audit trail of all actions that create, modify, or delete data. The audit trail is append-only and cannot be modified or deleted through the application interface.

The audit trail records: the authenticated user, the action performed, the affected record and its identifier, the previous state where applicable, the new state, and the timestamp. This applies to all admin actions, all ingestion events, all intervention log entries, all snapshot deletions, all threshold changes, and all user account changes.

The audit trail is accessible to admins from the Admin section. It is exportable to CSV or Excel.

### Infrastructure Security

The platform is hosted on a managed cloud platform. The following infrastructure security practices apply:

- Environment variables are used for all secrets — database credentials, API keys, secret keys. Secrets are never hardcoded in source code or committed to version control.
- Database access is restricted to the application server. The database is not publicly accessible.
- Dependency versions are pinned and reviewed regularly for known vulnerabilities.
- The production environment is separated from development and staging environments. Production data is never used in development or staging.

### Security in the Context of Initiative Modules

Initiative modules are subject to the same security requirements as the core platform. The initiative isolation boundary — writing only to their own tables, reading from core through defined interfaces — also functions as a security boundary. A vulnerability in an initiative module cannot directly modify core data. It can only affect core data through the defined read interfaces and the initiative ID field on the intervention log.

---

*End of Document*

**Version:** 1.0 | **Status:** Draft | **Date:** May 2026 | **Classification:** Confidential
