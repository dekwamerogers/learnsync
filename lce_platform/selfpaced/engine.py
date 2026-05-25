"""
Ingestion pipeline for the self-paced platform.

Phases:
  1. Parse CSV rows into cleaned dicts — Python only, no DB
  2. Resolve catalogue — detect programme+course once per unique eHub class name,
     bulk-query/create missing Courses and Assignments
  3. Bulk-upsert Learner records
  4. Bulk-upsert Enrolment + CourseEnrolment, then replace AssignmentProgress
  5. Compute health flags (bulk-prefetched)
  6. Create EnrolmentSnapshot per touched enrolment
  7. Finalise IngestionJob counters
"""

import logging
import re
from collections import defaultdict
from datetime import date, datetime

from django.db import models, transaction
from django.db.models import Count

from selfpaced.detector import detect_programme_and_course
from selfpaced.health import compute_enrolment_health
from selfpaced.parsing import (
    load_csv, parse_other_programme_names, row_to_dict, validate_columns,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Recompute-health progress state — updated in-place by recompute_health().
# Readable from any thread via get_recompute_status().
# ---------------------------------------------------------------------------

_recompute_state: dict = {
    'running': False,
    'total': 0,
    'done': 0,
    'errors': 0,
    'started_at': None,
    'finished_at': None,
    'scope': None,   # None = all, 'PROG:AICE' = programme, 'JOB:42' = ingestion job
}


def get_recompute_status() -> dict:
    return dict(_recompute_state)


def mark_recompute_starting(scope: str | None = None) -> None:
    """Pre-mark as running in the request thread before the background thread starts.
    This eliminates the race where the page reloads before the thread sets running=True."""
    _recompute_state['running'] = True
    _recompute_state['total'] = 0
    _recompute_state['done'] = 0
    _recompute_state['errors'] = 0
    _recompute_state['started_at'] = datetime.now().strftime('%H:%M:%S')
    _recompute_state['finished_at'] = None
    _recompute_state['scope'] = scope


# ---------------------------------------------------------------------------
# Constants + helpers
# ---------------------------------------------------------------------------

PIPELINE_STEPS = [
    'Parsing CSV',
    'Building course catalogue',
    'Upserting learner records',
    'Upserting enrolments & progress',
    'Computing health flags',
    'Creating snapshots',
]

# Placeholder names written when no real name was available.
# Silently upgraded if the CSV later provides a real name.
_PLACEHOLDER_RE = re.compile(r'^(?:[A-Z][A-Z0-9]* — )?Course \d+$', re.IGNORECASE)

# eHub sometimes prepends "PROG-SEQ: " to the course name column — strip it.
_COURSE_PREFIX_RE = re.compile(r'^[A-Za-z0-9]+-\d+:\s*')

# Format B eHub suffix may encode the course number: "WALX_C#1" → C#1 → 1.
# Used as the primary seq source when the Format A pattern doesn't match,
# because the CSV "Course sequence number" column is a global counter
# (WALX=1 globally, so CC-1 shows as 2 there), not a within-programme seq.
_SUFFIX_SEQ_RE = re.compile(r'[Cc]#(\d+)')


def _is_placeholder(name: str) -> bool:
    return not name or bool(_PLACEHOLDER_RE.match(name.strip()))


def _chunked(iterable, size: int = 500):
    """Yield successive fixed-size chunks. Keeps __in queries under SQLite's variable limit."""
    lst = list(iterable)
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _resolve_course_seq(ehub: str, csv_seq: int, fa_re) -> int:
    """
    Return the within-programme course sequence number.

    Priority:
      1. Format A (PROG-SEQ_anything): seq embedded in the eHub name — always authoritative.
      2. Format B suffix C#N (e.g. WALX_C#1): course number encoded after the underscore.
      3. CSV column value: last resort; unreliable because it is a global journey counter.
    """
    m = fa_re.match(ehub)
    if m:
        return int(m.group(2))
    m2 = _SUFFIX_SEQ_RE.search(ehub)
    if m2:
        return int(m2.group(1))
    return csv_seq


def _clean_course_name(name: str) -> str:
    if not name:
        return name
    return _COURSE_PREFIX_RE.sub('', name.strip())


def _emit_progress(job, step: int, msg: str) -> None:
    from datetime import datetime
    from selfpaced.models import IngestionJob
    entry = {'step': step, 'msg': msg, 'at': datetime.utcnow().strftime('%H:%M:%S')}
    job.progress_log = list(job.progress_log) + [entry]
    IngestionJob.objects.filter(pk=job.pk).update(progress_log=job.progress_log)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_ingestion(job_id: int) -> None:
    from selfpaced.models import IngestionJob
    job = IngestionJob.objects.get(pk=job_id)
    job.status = 'processing'
    job.progress_log = []
    job.save(update_fields=['status', 'progress_log'])
    try:
        content = bytes(job.file_content)
        _execute(job, content)
    except Exception as exc:
        logger.exception('IngestionJob %d failed: %s', job_id, exc)
        job.status = 'failed'
        job.errors = [str(exc)]
        job.save(update_fields=['status', 'errors'])
        raise


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _execute(job, content: bytes) -> None:
    from selfpaced.models import (
        Assignment, AssignmentProgress, Course, CourseEnrolment,
        Enrolment, FlagCode, FlaggedRow, Learner, PaymentStatus,
    )

    # Use the date the CSV was exported from the source system (set by the user
    # on the upload form). Falling back to today ensures old code paths still
    # work, but uploading with the correct export date is strongly preferred so
    # that "days since activity" flags are anchored to reality.
    upload_date: date = job.data_as_of_date or date.today()

    # ------------------------------------------------------------------
    # Phase 1 — Parse
    # ------------------------------------------------------------------
    headers, raw_rows = load_csv(content)
    col_errors = validate_columns(headers)
    if col_errors:
        job.status = 'failed'
        job.errors = col_errors
        job.save(update_fields=['status', 'errors'])
        return

    all_parsed = []   # all valid-email rows, unfiltered — used for catalogue discovery
    skip_count = 0
    for raw in raw_rows:
        r = row_to_dict(raw)
        if not r['email'] or '@' not in r['email']:
            skip_count += 1
            continue
        all_parsed.append(r)

    # Filter rows to monitored countries only for learner-data phases (3+).
    # Rows with a blank country pass through (can't determine origin).
    # Catalogue phases (2a-2b) use all_parsed so that courses/assignments
    # belonging entirely to unmonitored-country learners are still created.
    from selfpaced.models import MonitoredCountry as _MC
    _active_countries = _MC.active_names_lower()
    if _active_countries:
        parsed = [
            r for r in all_parsed
            if not r.get('country') or r['country'].lower() in _active_countries
        ]
        country_skip = len(all_parsed) - len(parsed)
        skip_count += country_skip
    else:
        parsed = all_parsed
        country_skip = 0

    _emit_progress(job, 1, f'Parsed {len(parsed):,} rows — {skip_count} skipped'
                   + (f' ({country_skip} unmonitored country)' if country_skip else ''))

    # ------------------------------------------------------------------
    # Phase 2 — Resolve catalogue
    # ------------------------------------------------------------------

    # 2a. Detect once per unique eHub class name (may auto-create Programmes).
    #     Use first non-blank course name seen for that eHub as the hint.
    #     Uses all_parsed so new courses/programmes from unmonitored-country
    #     rows are still detected and created.
    ehub_course_hint: dict[str, str] = {}
    for r in all_parsed:
        ehub = r['ehub_class_name']
        if ehub and ehub not in ehub_course_hint:
            ehub_course_hint[ehub] = _clean_course_name(r['course_name'] or '')

    ehub_resolution: dict[str, tuple] = {}   # ehub -> (programme, course | None)
    for ehub, hint in ehub_course_hint.items():
        prog, course = detect_programme_and_course(ehub, hint)
        ehub_resolution[ehub] = (prog, course)

    # Load course overrides set by the admin on the review screen.
    # Format: {'PROG_CODE|seq': target_course_pk, ...}
    _course_overrides_raw: dict[str, int] = (job.review_data or {}).get('course_overrides', {})
    # Convert to {(prog_pk, seq): target_course_pk} once we have programme pks (done after 2a).

    # 2b. Collect (programme_pk, seq) pairs where the course is still None.
    #     For Format A eHub names the seq is embedded; for Format B it comes from the CSV column.
    #     Uses all_parsed for the same reason as 2a.
    from selfpaced.detector import _EHUB_PATTERN as _FA_RE
    needed: dict[tuple, str] = {}   # (prog_pk, seq) -> full_name
    for r in all_parsed:
        ehub = r['ehub_class_name']
        prog, course = ehub_resolution.get(ehub, (None, None))
        if prog is None or course is not None:
            continue
        seq = _resolve_course_seq(ehub, r['course_sequence_number'], _FA_RE)
        if not seq:
            continue
        cname = _clean_course_name(r['course_name'] or '') or f'{prog.code} — Course {seq}'
        key = (prog.pk, seq)
        if key not in needed or _is_placeholder(needed[key]):
            needed[key] = cname

    # 2c. Bulk-query existing courses for all needed (prog, seq) pairs.
    if needed:
        existing_courses: dict[tuple, Course] = {
            (c.programme_id, c.sequence_number): c
            for c in Course.objects.filter(
                programme_id__in={k[0] for k in needed},
                sequence_number__in={k[1] for k in needed},
            )
        }
    else:
        existing_courses = {}

    # 2d. Create missing courses (one by one — usually very few per upload).
    prog_cache: dict[int, object] = {
        p.pk: p
        for p, _ in ehub_resolution.values()
        if p is not None
    }

    # Resolve course_overrides: map prog code|seq → (prog_pk, seq) key using prog_cache.
    prog_code_to_pk = {p.code: pk for pk, p in prog_cache.items()}
    course_overrides: dict[tuple, int] = {}   # (prog_pk, seq) -> target_course_pk
    for key_str, target_pk in _course_overrides_raw.items():
        parts = key_str.split('|', 1)
        if len(parts) == 2:
            prog_pk = prog_code_to_pk.get(parts[0].upper())
            try:
                seq = int(parts[1])
            except ValueError:
                continue
            if prog_pk:
                course_overrides[(prog_pk, seq)] = target_pk

    # Apply overrides: redirect (prog_pk, seq) → existing target course.
    if course_overrides:
        target_pks = set(course_overrides.values())
        override_courses: dict[int, Course] = {
            c.pk: c for c in Course.objects.filter(pk__in=target_pks)
        }
        for key, target_pk in course_overrides.items():
            if key in needed and target_pk in override_courses:
                existing_courses[key] = override_courses[target_pk]

    new_course_count = 0
    new_course_keys: set[tuple] = set()   # (prog_pk, seq) for courses created this run
    for (prog_pk, seq), cname in needed.items():
        if (prog_pk, seq) not in existing_courses:
            prog = prog_cache[prog_pk]
            c = Course.objects.create(
                programme=prog,
                sequence_number=seq,
                full_name=cname,
                code=f'{prog.code}-{seq}',
            )
            existing_courses[(prog_pk, seq)] = c
            new_course_keys.add((prog_pk, seq))
            new_course_count += 1

    # Register eHub patterns for newly created courses so future uploads use the
    # fast registry lookup instead of regex re-detection.
    if new_course_keys:
        from selfpaced.models import ProgrammeIdentifierRegistry as _PIR
        _existing_patterns: set[str] = set(
            _PIR.objects.filter(pattern_type='ehub_class_name').values_list('raw_pattern', flat=True)
        )
        _new_registry: dict[str, object] = {}
        for r in all_parsed:
            ehub = (r['ehub_class_name'] or '').strip()
            if not ehub or ehub in _existing_patterns or ehub in _new_registry:
                continue
            prog, course = ehub_resolution.get(ehub, (None, None))
            if prog is None or course is not None:
                continue
            seq = _resolve_course_seq(ehub, r['course_sequence_number'], _FA_RE)
            if not seq or (prog.pk, seq) not in new_course_keys:
                continue
            resolved = existing_courses.get((prog.pk, seq))
            if resolved:
                _new_registry[ehub] = _PIR(
                    raw_pattern=ehub,
                    pattern_type='ehub_class_name',
                    programme=prog,
                    course=resolved,
                )
        if _new_registry:
            _PIR.objects.bulk_create(list(_new_registry.values()), ignore_conflicts=True)

    # 2e. Upgrade placeholder names on existing courses where the CSV has a real name.
    upgraded: set[int] = set()
    for r in parsed:
        ehub = r['ehub_class_name']
        prog, course = ehub_resolution.get(ehub, (None, None))
        if prog is None:
            continue
        if course is None:
            seq = _resolve_course_seq(ehub, r['course_sequence_number'], _FA_RE)
            course = existing_courses.get((prog.pk, seq)) if seq else None
        if course is None or course.pk in upgraded:
            continue
        cname = _clean_course_name(r['course_name'] or '')
        if cname and course.full_name != cname and _is_placeholder(course.full_name):
            Course.objects.filter(pk=course.pk).update(full_name=cname)
            course.full_name = cname
            upgraded.add(course.pk)

    # 2f. Group rows by (email, prog_pk, course_pk); flag unresolvable rows.
    row_groups: dict[tuple, list] = defaultdict(list)
    flagged_count = 0

    for r in parsed:
        ehub = r['ehub_class_name']
        prog, course = ehub_resolution.get(ehub, (None, None))

        if prog is None:
            FlaggedRow.objects.create(
                job=job,
                raw_data={'email': r['email'], 'ehub_class_name': ehub},
                flag_reason='unrecognised_pattern',
            )
            flagged_count += 1
            continue

        if course is None:
            seq = _resolve_course_seq(ehub, r['course_sequence_number'], _FA_RE)
            if not seq:
                FlaggedRow.objects.create(
                    job=job,
                    raw_data={'email': r['email'], 'ehub_class_name': ehub},
                    flag_reason='missing_sequence_number',
                )
                flagged_count += 1
                continue
            course = existing_courses.get((prog.pk, seq))
            if course is None:
                FlaggedRow.objects.create(
                    job=job,
                    raw_data={'email': r['email'], 'ehub_class_name': ehub, 'seq': seq},
                    flag_reason='unresolved_course',
                )
                flagged_count += 1
                continue

        row_groups[(r['email'], prog.pk, course.pk)].append(r)

    # 2g. Bulk-query + create Assignments.
    # Uses all_parsed (not row_groups) so assignments are created for courses
    # whose rows were entirely filtered out by the country filter.
    needed_assigns: dict[tuple, str] = {}   # (course_pk, name) -> type
    for r in all_parsed:
        aname = r['assignment_name']
        if not aname:
            continue
        ehub = r['ehub_class_name']
        prog, course = ehub_resolution.get(ehub, (None, None))
        if prog is None:
            continue
        if course is None:
            seq = _resolve_course_seq(ehub, r['course_sequence_number'], _FA_RE)
            course = existing_courses.get((prog.pk, seq)) if seq else None
        if course is None:
            continue
        needed_assigns[(course.pk, aname)] = r['assignment_type']

    if needed_assigns:
        existing_assigns: dict[tuple, Assignment] = {
            (a.course_id, a.name): a
            for a in Assignment.objects.filter(
                course_id__in={k[0] for k in needed_assigns}
            )
        }
        to_create = [
            Assignment(
                course_id=course_pk,
                name=aname,
                type=atype,
                sequence_in_course=0,
            )
            for (course_pk, aname), atype in needed_assigns.items()
            if (course_pk, aname) not in existing_assigns
        ]
        if to_create:
            Assignment.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=500)
        all_assigns: dict[tuple, Assignment] = {
            (a.course_id, a.name): a
            for a in Assignment.objects.filter(
                course_id__in={k[0] for k in needed_assigns}
            )
        }
        new_assignment_count = len(to_create)
    else:
        all_assigns = {}
        new_assignment_count = 0

    _emit_progress(
        job, 2,
        f'{new_course_count} new course{"s" if new_course_count != 1 else ""}, '
        f'{new_assignment_count} new assignment{"s" if new_assignment_count != 1 else ""}'
        + (f' — {flagged_count} flagged' if flagged_count else ''),
    )

    # ------------------------------------------------------------------
    # Phase 3 — Bulk-upsert Learners
    # ------------------------------------------------------------------
    all_emails: set[str] = {email for (email, _, _) in row_groups}

    # Latest row per email (last group entry wins for learner-level fields).
    latest_by_email: dict[str, dict] = {}
    for (email, _, _), rows in row_groups.items():
        latest_by_email[email] = rows[-1]

    # Fetch existing learners: email → pk (needed for bulk_update below)
    existing_email_to_pk: dict[str, int] = {}
    for _chunk in _chunked(list(all_emails)):
        existing_email_to_pk.update(
            Learner.objects.filter(email__in=_chunk).values_list('email', 'pk')
        )
    existing_emails: set[str] = set(existing_email_to_pk.keys())

    _LEARNER_UPDATE_FIELDS = [
        'first_name', 'last_name', 'gender', 'country', 'region',
        'ehub_profile_url', 'lms_profile_url',
        'has_logged_into_ehub', 'has_logged_into_lms', 'has_shown_up_in_course',
        'other_programmes_count', 'other_programme_names', 'payment_status',
    ]

    learner_objs = [
        Learner(
            email=email,
            first_name=r['first_name'],
            last_name=r['last_name'],
            gender=r['gender'],
            country=r['country'],
            region=r['region'],
            ehub_profile_url=r['ehub_profile_url'],
            lms_profile_url=r['lms_profile_url'],
            has_logged_into_ehub=r['has_logged_into_ehub'],
            has_logged_into_lms=r['has_logged_into_lms'],
            has_shown_up_in_course=r['has_shown_up_in_course'],
            other_programmes_count=r['other_programmes_count'],
            other_programme_names=r['other_programme_names'],
            payment_status=r['payment_status'],
            first_seen_date=upload_date,  # excluded from update_fields → only set on INSERT
        )
        for email, r in latest_by_email.items()
    ]

    # MariaDB / MySQL does not support bulk_create(update_conflicts=True, unique_fields=…)
    # (that syntax is PostgreSQL-only).  Split manually instead.
    new_objs    = [o for o in learner_objs if o.email not in existing_emails]
    update_objs = [o for o in learner_objs if o.email     in existing_emails]

    if new_objs:
        Learner.objects.bulk_create(new_objs, batch_size=500)

    if update_objs:
        for o in update_objs:
            o.pk = existing_email_to_pk[o.email]
        Learner.objects.bulk_update(update_objs, _LEARNER_UPDATE_FIELDS, batch_size=500)

    new_learners = len(all_emails - existing_emails)
    updated_learners = len(all_emails & existing_emails)

    _emit_progress(
        job, 3,
        f'{new_learners:,} new learner{"s" if new_learners != 1 else ""}, '
        f'{updated_learners:,} updated',
    )

    # ------------------------------------------------------------------
    # Phase 4 — Enrolments, CourseEnrolments, AssignmentProgress
    # ------------------------------------------------------------------

    all_prog_pks: set[int] = {prog_pk for (_, prog_pk, _) in row_groups}

    # --- 4a. Enrolments ---

    # Aggregate enrolment-level fields across all course groups for this learner × programme.
    enrolment_agg: dict[tuple, dict] = {}   # (email, prog_pk) -> aggregated fields
    for (email, prog_pk, _), rows in row_groups.items():
        key = (email, prog_pk)
        if key not in enrolment_agg:
            enrolment_agg[key] = {
                'fsol': [], 'act': [],
                'is_graduated': False,
                'graduation_date': None,
                'is_graduated_on_savanna': False,
            }
        agg = enrolment_agg[key]
        for r in rows:
            if r['first_sign_of_life_date']:
                agg['fsol'].append(r['first_sign_of_life_date'])
            if r['activation_date']:
                agg['act'].append(r['activation_date'])
        last = rows[-1]
        if last['is_programme_graduated']:
            agg['is_graduated'] = True
        if last['programme_graduation_date']:
            agg['graduation_date'] = last['programme_graduation_date']
        if last['is_graduated_on_savanna']:
            agg['is_graduated_on_savanna'] = True

    # Existing enrolments — chunked to avoid SQLite 999-variable limit
    existing_enrolments: dict[tuple, Enrolment] = {}
    for _chunk in _chunked(list(all_emails)):
        for e in Enrolment.objects.filter(
            learner__email__in=_chunk,
            programme_id__in=all_prog_pks,
        ):
            existing_enrolments[(e.learner_id, e.programme_id)] = e

    new_enrolment_objs = []
    for (email, prog_pk), agg in enrolment_agg.items():
        if (email, prog_pk) in existing_enrolments:
            continue
        new_enrolment_objs.append(Enrolment(
            learner_id=email,
            programme_id=prog_pk,
            first_sign_of_life_date=min(agg['fsol']) if agg['fsol'] else None,
            activation_date=min(agg['act']) if agg['act'] else None,
            is_graduated=agg['is_graduated'],
            graduation_date=agg['graduation_date'],
            is_graduated_on_savanna=agg['is_graduated_on_savanna'],
            created_by_job=job,
        ))

    if new_enrolment_objs:
        Enrolment.objects.bulk_create(new_enrolment_objs, batch_size=500)

    # Update existing enrolments.
    # Guards: never overwrite a later/better value with an older CSV's data.
    #   • FSOL / activation: keep the EARLIEST known date (min of existing + CSV).
    #   • Graduation: once graduated, always graduated — never regress.
    update_enrolment_objs = []
    for (email, prog_pk), agg in enrolment_agg.items():
        e = existing_enrolments.get((email, prog_pk))
        if e is None:
            continue
        if agg['fsol']:
            csv_fsol = min(agg['fsol'])
            e.first_sign_of_life_date = (
                min(e.first_sign_of_life_date, csv_fsol)
                if e.first_sign_of_life_date else csv_fsol
            )
        if agg['act']:
            csv_act = min(agg['act'])
            e.activation_date = (
                min(e.activation_date, csv_act)
                if e.activation_date else csv_act
            )
        # Don't regress from graduated — once True it stays True.
        if agg['is_graduated']:
            e.is_graduated = True
        if agg['graduation_date']:
            e.graduation_date = agg['graduation_date']
        if agg['is_graduated_on_savanna']:
            e.is_graduated_on_savanna = True
        update_enrolment_objs.append(e)

    if update_enrolment_objs:
        Enrolment.objects.bulk_update(
            update_enrolment_objs,
            ['first_sign_of_life_date', 'activation_date',
             'is_graduated', 'graduation_date', 'is_graduated_on_savanna'],
            batch_size=500,
        )

    # Re-fetch all enrolments to get PKs (including newly created) — chunked.
    all_enrolments: dict[tuple, Enrolment] = {}
    for _chunk in _chunked(list(all_emails)):
        for e in Enrolment.objects.filter(
            learner__email__in=_chunk,
            programme_id__in=all_prog_pks,
        ).select_related('programme', 'learner'):
            all_enrolments[(e.learner_id, e.programme_id)] = e

    # --- 4b. CourseEnrolments ---

    all_course_pks: set[int] = {course_pk for (_, _, course_pk) in row_groups}

    all_enrolment_pks = {e.pk for e in all_enrolments.values()}
    existing_ces: dict[tuple, CourseEnrolment] = {}
    for e_chunk in _chunked(all_enrolment_pks):
        for c_chunk in _chunked(all_course_pks):
            for ce in CourseEnrolment.objects.filter(
                enrolment_id__in=e_chunk,
                course_id__in=c_chunk,
            ):
                existing_ces[(ce.enrolment_id, ce.course_id)] = ce

    new_ce_objs = []
    update_ce_objs = []

    for (email, prog_pk, course_pk), rows in row_groups.items():
        enrolment = all_enrolments.get((email, prog_pk))
        if enrolment is None:
            continue

        last_r = rows[-1]
        activity_dates = [
            d for r in rows
            for d in (r['assignment_accessed_date'], r['assignment_submitted_date'])
            if d
        ]
        last_activity = max(activity_dates) if activity_dates else None

        submitted = [r for r in rows if r['is_assignment_submitted']]
        passed = [r for r in submitted if r['is_assignment_passed']]
        pass_pct = round(len(passed) / len(submitted) * 100) if submitted else None

        if last_r['is_course_graduated']:
            status = 'completed'
            is_passed = True
            completion_date = last_r['course_graduation_date']
        else:
            status = 'in_progress'
            is_passed = False
            completion_date = None

        opt_in = min(
            (d for d in [last_r['activation_date'], last_r['first_sign_of_life_date']] if d),
            default=None,
        )

        ce_key = (enrolment.pk, course_pk)
        if ce_key in existing_ces:
            ce = existing_ces[ce_key]
            # Don't regress from completed — an old CSV won't show the completion.
            if status == 'completed' or ce.status != 'completed':
                ce.status = status
                ce.is_passed = is_passed
                # Don't clear a completion date that's already recorded.
                if completion_date or ce.completion_date is None:
                    ce.completion_date = completion_date
            # Only advance last_activity — never move it backwards with old data.
            if last_activity and (ce.last_activity_date is None or last_activity > ce.last_activity_date):
                ce.last_activity_date = last_activity
            ce.pass_percentage = pass_pct
            # Don't clear an existing opt-in date with a later upload's None.
            if opt_in and (ce.opt_in_date is None or opt_in < ce.opt_in_date):
                ce.opt_in_date = opt_in
            update_ce_objs.append(ce)
        else:
            new_ce_objs.append(CourseEnrolment(
                enrolment=enrolment,
                course_id=course_pk,
                status=status,
                is_passed=is_passed,
                completion_date=completion_date,
                last_activity_date=last_activity,
                pass_percentage=pass_pct,
                opt_in_date=opt_in,
            ))

    if new_ce_objs:
        CourseEnrolment.objects.bulk_create(new_ce_objs, batch_size=500)
    if update_ce_objs:
        CourseEnrolment.objects.bulk_update(
            update_ce_objs,
            ['status', 'is_passed', 'completion_date',
             'last_activity_date', 'pass_percentage', 'opt_in_date'],
            batch_size=500,
        )

    # Re-fetch all CEs with course info for current_course and health phases.
    all_ces = []
    for e_chunk in _chunked(all_enrolment_pks):
        all_ces.extend(
            CourseEnrolment.objects.filter(
                enrolment_id__in=e_chunk
            ).select_related('course')
        )
    ces_by_enrolment: dict[int, list] = defaultdict(list)
    for ce in all_ces:
        ces_by_enrolment[ce.enrolment_id].append(ce)
    ce_pk_map: dict[tuple, int] = {(ce.enrolment_id, ce.course_id): ce.pk for ce in all_ces}

    # Infer completions: enrolment in course N implies courses 1…N-1 are complete.
    # The CSV only sets is_course_graduated on the course the learner last touched,
    # so lower-sequence courses are left as in_progress even when the learner has moved on.
    _infer_updates = _infer_course_completions(ces_by_enrolment)
    if _infer_updates:
        CourseEnrolment.objects.bulk_update(_infer_updates, ['status', 'is_passed'], batch_size=500)

    _grad_ce_updates = _complete_graduated_enrolment_ces(
        all_enrolments.values(), ces_by_enrolment
    )
    if _grad_ce_updates:
        CourseEnrolment.objects.bulk_update(_grad_ce_updates, ['status', 'is_passed'], batch_size=500)

    # Set current_course to highest in-progress sequence number.
    enrolments_to_update_cc = []
    for enrolment in all_enrolments.values():
        in_progress = [ce for ce in ces_by_enrolment[enrolment.pk] if ce.status == 'in_progress']
        if in_progress:
            highest = max(in_progress, key=lambda ce: ce.course.sequence_number)
            if enrolment.current_course_id != highest.course_id:
                enrolment.current_course_id = highest.course_id
                enrolments_to_update_cc.append(enrolment)

    if enrolments_to_update_cc:
        Enrolment.objects.bulk_update(enrolments_to_update_cc, ['current_course'], batch_size=500)

    # --- 4c. Programme graduation derivation ---
    # If CSV doesn't mark the learner as graduated but all courses are completed, derive it.
    prog_course_counts: dict[int, int] = {
        row['programme_id']: row['n']
        for row in Course.objects.filter(is_active=True)
        .values('programme_id')
        .annotate(n=Count('id'))
    }

    grad_updates = []
    for enrolment in all_enrolments.values():
        if enrolment.is_graduated:
            continue
        ces = ces_by_enrolment[enrolment.pk]
        completed = [ce for ce in ces if ce.status == 'completed']
        total = prog_course_counts.get(enrolment.programme_id, len(ces))
        if total and len(completed) >= total:
            completion_dates = [ce.completion_date for ce in completed if ce.completion_date]
            enrolment.is_graduated = True
            enrolment.graduation_date = max(completion_dates) if completion_dates else None
            grad_updates.append(enrolment)

    if grad_updates:
        Enrolment.objects.bulk_update(grad_updates, ['is_graduated', 'graduation_date'], batch_size=500)

    # --- 4d. Replace AssignmentProgress ---
    touched_ce_pks: set[int] = {ce.pk for ce in all_ces}

    # Only delete and rebuild AP for courses that appear in this upload.
    # touched_ce_pks includes ALL courses for touched learners (needed for Phase 5
    # health computation), but wiping AP for courses not in this CSV would
    # permanently erase completed-course history on every re-upload.
    upload_ce_pks: set[int] = set()
    for (email, prog_pk, course_pk) in row_groups:
        enrolment = all_enrolments.get((email, prog_pk))
        if enrolment is None:
            continue
        ce_pk = ce_pk_map.get((enrolment.pk, course_pk))
        if ce_pk is not None:
            upload_ce_pks.add(ce_pk)

    # Preserve passed_on_first_attempt across re-uploads.
    prev_pfa: dict[tuple, bool] = {}
    for chunk in _chunked(upload_ce_pks):
        for row in AssignmentProgress.objects.filter(
            course_enrolment_id__in=chunk,
            passed_on_first_attempt=True,
        ).values('course_enrolment_id', 'assignment_id', 'passed_on_first_attempt'):
            prev_pfa[(row['course_enrolment_id'], row['assignment_id'])] = True

    for chunk in _chunked(upload_ce_pks):
        AssignmentProgress.objects.filter(course_enrolment_id__in=chunk).delete()

    # Use a dict keyed by (ce_pk, assign_pk) so duplicate CSV rows
    # (same learner + course + assignment appearing more than once) are
    # naturally deduplicated — the last row seen wins.
    ap_map: dict[tuple, AssignmentProgress] = {}
    for (email, prog_pk, course_pk), rows in row_groups.items():
        enrolment = all_enrolments.get((email, prog_pk))
        if enrolment is None:
            continue
        ce_pk = ce_pk_map.get((enrolment.pk, course_pk))
        if ce_pk is None:
            continue
        for r in rows:
            aname = r['assignment_name']
            if not aname:
                continue
            assign = all_assigns.get((course_pk, aname))
            if assign is None:
                continue
            ap_map[(ce_pk, assign.pk)] = AssignmentProgress(
                course_enrolment_id=ce_pk,
                assignment=assign,
                is_accessed=r['is_assignment_accessed'],
                accessed_date=r['assignment_accessed_date'],
                is_submitted=r['is_assignment_submitted'],
                submitted_date=r['assignment_submitted_date'],
                is_passed=r['is_assignment_passed'],
                passed_on_first_attempt=prev_pfa.get((ce_pk, assign.pk), False) or r['passed_on_first_attempt'],
                attempt_count=1 if r['is_assignment_submitted'] else 0,
            )

    if ap_map:
        AssignmentProgress.objects.bulk_create(list(ap_map.values()), batch_size=500)

    _emit_progress(
        job, 4,
        f'{len(all_enrolments):,} enrolment{"s" if len(all_enrolments) != 1 else ""} updated'
        + (f' — {flagged_count} flagged' if flagged_count else ''),
    )

    # ------------------------------------------------------------------
    # Phase 4c — Auto-graduate prerequisite programmes (e.g. WALX) for
    # learners who appear in this CSV with substantive course activity
    # but have NO prerequisite rows.
    #
    # When eHub graduates a learner from WALX, their WALX rows stop
    # appearing in the activity export. A learner with substantive
    # programme data and no WALX rows has therefore almost certainly
    # been graduated from WALX on the LMS already.
    # ------------------------------------------------------------------
    from selfpaced.models import Programme as _ProgModel
    prereq_progs = list(_ProgModel.objects.filter(is_prerequisite=True))

    if prereq_progs:
        prereq_prog_pks = {p.pk for p in prereq_progs}

        # Emails that had at least one prerequisite row in this CSV
        prereq_in_csv = {
            email for (email, prog_pk, _) in row_groups
            if prog_pk in prereq_prog_pks
        }

        # Learners with substantive activity but no prerequisite rows
        need_walx_grad = all_emails - prereq_in_csv

        if need_walx_grad:
            learner_pk_by_email = {
                l.email: l.pk
                for l in Learner.objects.filter(email__in=need_walx_grad)
            }
            all_learner_pks = set(learner_pk_by_email.values())

            for prereq_prog in prereq_progs:
                existing_walx = {
                    e.learner_id: e
                    for e in Enrolment.objects.filter(
                        learner_id__in=all_learner_pks,
                        programme=prereq_prog,
                    )
                }

                to_update = []
                to_create = []

                for email, learner_pk in learner_pk_by_email.items():
                    if learner_pk in existing_walx:
                        e = existing_walx[learner_pk]
                        if not e.is_graduated:
                            e.is_graduated = True
                            to_update.append(e)
                    else:
                        to_create.append(Enrolment(
                            learner_id=learner_pk,
                            programme=prereq_prog,
                            is_graduated=True,
                            created_by_job=job,
                        ))

                if to_update:
                    Enrolment.objects.bulk_update(to_update, ['is_graduated'], batch_size=500)
                if to_create:
                    Enrolment.objects.bulk_create(
                        to_create, batch_size=500, ignore_conflicts=True,
                    )

    # ------------------------------------------------------------------
    # Phase 5 — Health flags
    # ------------------------------------------------------------------
    touched_enrolments = list(all_enrolments.values())

    aps_by_ce: dict[int, list] = defaultdict(list)
    for chunk in _chunked(touched_ce_pks):
        for ap in AssignmentProgress.objects.filter(
            course_enrolment_id__in=chunk
        ).select_related('assignment'):
            aps_by_ce[ap.course_enrolment_id].append(ap)

    # Pre-fetch learner → set of enrolment PKs that have any in_progress/completed CE.
    # Eliminates the N+1 DB query inside flag_stalled_progression.
    all_learner_pks = {e.learner_id for e in touched_enrolments}
    learner_active_enrolment_pks: dict[int, set[int]] = defaultdict(set)
    for chunk in _chunked(all_learner_pks):
        for row in (
            CourseEnrolment.objects
            .filter(
                enrolment__learner_id__in=chunk,
                status__in=['in_progress', 'completed'],
            )
            .values('enrolment_id', 'enrolment__learner_id')
        ):
            learner_active_enrolment_pks[row['enrolment__learner_id']].add(row['enrolment_id'])

    # Pre-fetch payment status so payment-forced at_risk is reflected in Enrolment.health_status.
    _payment_issue_statuses = {PaymentStatus.DUE_SOON, PaymentStatus.GRACE_PERIOD, PaymentStatus.OVERDUE}
    learner_payment: dict = {}
    for chunk in _chunked(all_learner_pks):
        learner_payment.update(
            Learner.objects.filter(email__in=chunk).values_list('email', 'payment_status')
        )

    threshold_cache: dict = {}
    health_errors = []

    for enrolment in touched_enrolments:
        try:
            ces = ces_by_enrolment.get(enrolment.pk, [])
            aps: list = []
            for ce in ces:
                aps.extend(aps_by_ce.get(ce.pk, []))
            if enrolment.programme_id not in threshold_cache:
                from selfpaced.models import ProgrammeThreshold as _PT
                threshold_cache[enrolment.programme_id] = _PT.for_programme(enrolment.programme)
            health_status, active_flags, flag_detail = compute_enrolment_health(
                enrolment, upload_date,
                prefetched_course_enrolments=ces,
                prefetched_all_progress=aps,
                prefetched_threshold=threshold_cache[enrolment.programme_id],
                programme_course_count=prog_course_counts.get(enrolment.programme_id),
                learner_active_enrolment_pks=learner_active_enrolment_pks,
            )
            # Payment issue is tracked as a flag but does not override health status —
            # engagement status (active, dormant, etc.) is kept independent of payment.
            if (learner_payment.get(enrolment.learner_id) in _payment_issue_statuses
                    and FlagCode.PAYMENT_ISSUE not in active_flags):
                active_flags = list(active_flags) + [FlagCode.PAYMENT_ISSUE]
            enrolment.health_status = health_status
            enrolment.active_flags = active_flags
            enrolment.flag_detail = flag_detail
        except Exception as exc:
            logger.exception('Health compute failed for enrolment %s: %s', enrolment.pk, exc)
            health_errors.append(f'{enrolment.learner_id} ({enrolment.programme.code}): {exc}')

    if touched_enrolments:
        Enrolment.objects.bulk_update(
            touched_enrolments,
            ['health_status', 'active_flags', 'flag_detail'],
            batch_size=500,
        )

    _update_learner_health_rollups(touched_enrolments)

    if health_errors:
        job.errors = list(job.errors or []) + health_errors
        job.save(update_fields=['errors'])

    _emit_progress(
        job, 5,
        f'Health flags computed for {len(touched_enrolments):,} enrolment'
        f'{"s" if len(touched_enrolments) != 1 else ""}'
        + (f' — {len(health_errors)} error{"s" if len(health_errors) != 1 else ""}' if health_errors else ''),
    )

    # ------------------------------------------------------------------
    # Phase 6 — Snapshots (bulk — avoids N+1 by using pre-fetched dicts)
    # ------------------------------------------------------------------
    from selfpaced.models import EnrolmentSnapshot as _Snap

    snapshot_objs = []
    for enrolment in touched_enrolments:
        ces  = ces_by_enrolment.get(enrolment.pk, [])
        aps  = [ap for ce in ces for ap in aps_by_ce.get(ce.pk, [])]

        courses_completed      = sum(1 for ce in ces if ce.status == 'completed')
        assignments_accessed   = sum(1 for ap in aps if ap.is_accessed)
        assignments_submitted  = sum(1 for ap in aps if ap.is_submitted)
        assignments_passed     = sum(1 for ap in aps if ap.is_passed)
        pass_rate = (
            assignments_passed / assignments_submitted * 100
            if assignments_submitted else None
        )

        activity_dates = [
            d for ap in aps
            for d in (ap.accessed_date, ap.submitted_date) if d
        ]
        last_activity      = max(activity_dates) if activity_dates else None
        days_since_act     = (upload_date - last_activity).days if last_activity else None
        fsol               = enrolment.first_sign_of_life_date
        days_since_fsol    = (upload_date - fsol).days if fsol else None

        # current_course sequence — read from already-loaded CE objects
        current_seq = None
        if enrolment.current_course_id:
            for ce in ces:
                if ce.course_id == enrolment.current_course_id:
                    current_seq = ce.course.sequence_number
                    break

        snapshot_objs.append(_Snap(
            learner_id=enrolment.learner_id,
            enrolment=enrolment,
            programme=enrolment.programme,
            ingestion_job=job,
            snapshot_date=upload_date,
            current_course_sequence=current_seq,
            courses_completed=courses_completed,
            assignments_accessed=assignments_accessed,
            assignments_submitted=assignments_submitted,
            assignments_passed=assignments_passed,
            pass_rate=pass_rate,
            last_activity_date=last_activity,
            days_since_last_activity=days_since_act,
            days_since_first_sign_of_life=days_since_fsol,
            health_status=enrolment.health_status,
            active_flags=enrolment.active_flags,
            payment_status=enrolment.learner.payment_status,
        ))

    if snapshot_objs:
        _Snap.objects.bulk_create(snapshot_objs, batch_size=500)

    _emit_progress(
        job, 6,
        f'{len(snapshot_objs):,} snapshot'
        f'{"s" if len(snapshot_objs) != 1 else ""} created',
    )

    # ------------------------------------------------------------------
    # Phase 7 — Finalise
    # ------------------------------------------------------------------
    job.status = 'complete'
    job.rows_processed = len(parsed) + skip_count
    job.new_learners = new_learners
    job.updated_learners = updated_learners
    job.new_assignments = new_assignment_count
    job.flagged_row_count = flagged_count
    job.warnings = []
    job.file_content = b''   # CSV no longer needed once data is normalised into the DB
    job.save(update_fields=[
        'status', 'rows_processed', 'new_learners', 'updated_learners',
        'new_assignments', 'flagged_row_count', 'warnings', 'file_content',
    ])


# ---------------------------------------------------------------------------
# Preview (read-mostly — creates Programmes via detector but not Courses)
# ---------------------------------------------------------------------------

def preview_ingestion(job_id: int) -> dict:
    from selfpaced.detector import _EHUB_PATTERN as _FA_RE
    from selfpaced.models import Assignment, Course, IngestionJob, Learner, Programme

    job = IngestionJob.objects.get(pk=job_id)
    content = bytes(job.file_content)

    headers, raw_rows = load_csv(content)
    col_errors = validate_columns(headers)
    if col_errors:
        job.status = 'failed'
        job.errors = col_errors
        job.save(update_fields=['status', 'errors'])
        return {}

    parsed = []
    skip_count = 0
    for raw in raw_rows:
        r = row_to_dict(raw)
        if not r['email'] or '@' not in r['email']:
            skip_count += 1
            continue
        parsed.append(r)

    # Cross-enrolment signals
    cross_enrolled: dict[str, set] = {}
    for r in parsed:
        for name in parse_other_programme_names(r.get('other_programme_names') or ''):
            cross_enrolled.setdefault(name, set()).add(r['email'])

    # Detect once per unique eHub class name (may auto-create Programmes).
    ehub_course_hint: dict[str, str] = {}
    for r in parsed:
        ehub = r['ehub_class_name']
        if ehub and ehub not in ehub_course_hint:
            ehub_course_hint[ehub] = _clean_course_name(r['course_name'] or '')

    ehub_resolution: dict[str, tuple] = {}
    for ehub, hint in ehub_course_hint.items():
        prog, course = detect_programme_and_course(ehub, hint)
        ehub_resolution[ehub] = (prog, course)

    # Collect needed (prog, seq) → name and count rows per programme.
    needed: dict[tuple, str] = {}     # (prog_pk, seq) -> name
    prog_row_counts: dict[str, int] = {}   # prog_code -> row count
    flagged_preview = []
    flagged_emails: set[str] = set()
    new_prog_codes: set[str] = set()

    # Which programme codes are already in the DB?
    existing_prog_codes = set(
        Programme.objects.values_list('code', flat=True)
    )

    for r in parsed:
        ehub = r['ehub_class_name']
        prog, course = ehub_resolution.get(ehub, (None, None))

        if prog is None:
            flagged_preview.append({
                'flag_reason': 'unrecognised_pattern',
                'raw_data': {'email': r['email'], 'ehub_class_name': ehub},
            })
            flagged_emails.add(r['email'])
            continue

        prog_row_counts[prog.code] = prog_row_counts.get(prog.code, 0) + 1

        if prog.code not in existing_prog_codes:
            new_prog_codes.add(prog.code)

        if course is None:
            seq = _resolve_course_seq(ehub, r['course_sequence_number'], _FA_RE)
            if seq:
                cname = _clean_course_name(r['course_name'] or '') or f'{prog.code} — Course {seq}'
                key = (prog.pk, seq)
                if key not in needed or _is_placeholder(needed[key]):
                    needed[key] = cname

    # Bulk-query existing courses.
    if needed:
        _course_rows = Course.objects.filter(
            programme_id__in={k[0] for k in needed}
        ).values('id', 'code', 'programme_id', 'sequence_number')
        existing_course_map: dict[tuple, dict] = {
            (row['programme_id'], row['sequence_number']): row
            for row in _course_rows
        }
        existing_course_pairs: set[tuple] = set(existing_course_map.keys())
    else:
        existing_course_map = {}
        existing_course_pairs = set()

    truly_new_courses = [
        {'programme_id': prog_pk, 'sequence_number': seq, 'full_name': name}
        for (prog_pk, seq), name in needed.items()
        if (prog_pk, seq) not in existing_course_pairs
    ]

    # Bulk-query existing assignments for a new-count estimate.
    if needed:
        needed_assign_keys: set[tuple] = set()
        prog_id_to_code = dict(Programme.objects.values_list('id', 'code'))
        for r in parsed:
            ehub = r['ehub_class_name']
            prog, course = ehub_resolution.get(ehub, (None, None))
            if prog and r['assignment_name']:
                seq = _resolve_course_seq(ehub, r['course_sequence_number'], _FA_RE)
                if seq:
                    needed_assign_keys.add((prog.pk, seq, r['assignment_name']))

        existing_assign_keys: set[tuple] = set(
            Assignment.objects.filter(
                course__programme_id__in={k[0] for k in needed}
            ).values_list('course__programme_id', 'course__sequence_number', 'name')
        )
        new_assign_count = len(needed_assign_keys - existing_assign_keys)
    else:
        new_assign_count = 0

    # Programme breakdown for the review card.
    prog_names = dict(Programme.objects.values_list('code', 'name'))
    new_course_by_prog: dict[str, int] = {}
    for (prog_pk, seq) in needed:
        if (prog_pk, seq) not in existing_course_pairs:
            prog_code = prog_id_to_code.get(prog_pk, '')
            new_course_by_prog[prog_code] = new_course_by_prog.get(prog_code, 0) + 1

    prog_courses: dict[str, list] = defaultdict(list)
    prog_id_map = {p.pk: p.code for p, _ in ehub_resolution.values() if p}
    for (prog_pk, seq), name in needed.items():
        prog_code = prog_id_map.get(prog_pk, '')
        is_new = (prog_pk, seq) not in existing_course_pairs
        existing_row = existing_course_map.get((prog_pk, seq)) or {}
        # Course tag shown on the review screen — prefer the stored code, fall back to derived form.
        course_tag = existing_row.get('code') or f'{prog_code}-{seq}'
        prog_courses[prog_code].append({
            'seq': seq,
            'name': name,
            'tag': course_tag,
            'is_new': is_new,
            'course_pk': existing_row.get('id'),
        })

    programme_breakdown = [
        {
            'code': code,
            'name': prog_names.get(code, code),
            'row_count': prog_row_counts[code],
            'courses': sorted(prog_courses.get(code, []), key=lambda c: c['seq']),
            'new_course_count': new_course_by_prog.get(code, 0),
            'is_new': code in new_prog_codes,
        }
        for code in sorted(prog_row_counts)
    ]

    # New vs existing learner count (exclude emails only seen in unresolvable rows).
    processable_emails = {r['email'] for r in parsed} - flagged_emails
    existing_emails: set[str] = set()
    for _chunk in _chunked(list(processable_emails)):
        existing_emails.update(
            Learner.objects.filter(email__in=_chunk).values_list('email', flat=True)
        )

    preview = {
        'rows_processed':     len(parsed) + skip_count,
        'new_learners':       len(processable_emails - existing_emails),
        'updated_learners':   len(processable_emails & existing_emails),
        'flagged_count':      len(flagged_preview),
        'warnings':           [],
        'new_programmes':     [{'code': c} for c in sorted(new_prog_codes)],
        'new_courses':        truly_new_courses,
        'new_assignments':    new_assign_count,
        'programme_breakdown': programme_breakdown,
        'flagged_rows':       flagged_preview[:50],
        'cross_enrolled_programmes': sorted(
            [{'name': n, 'learner_count': len(emails)} for n, emails in cross_enrolled.items()],
            key=lambda x: -x['learner_count'],
        ),
    }

    job.review_data = preview
    job.status = 'pending_review'
    job.save(update_fields=['review_data', 'status'])
    return preview


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_course_completions(ces_by_enrolment: dict) -> list:
    """
    Sequential programmes require completing course N before starting N+1.
    The CSV only marks is_course_graduated on the course the learner last touched,
    so lower-sequence courses are left as in_progress even after the learner advances.
    This function corrects that: any in_progress CE whose sequence number is below
    the highest sequence CE in the same enrolment is marked completed.
    Mutates CE objects in-place; returns the updated objects for bulk_update.
    """
    updates = []
    for ces in ces_by_enrolment.values():
        if len(ces) <= 1:
            continue
        max_seq = max(ce.course.sequence_number for ce in ces)
        for ce in ces:
            if ce.course.sequence_number < max_seq and ce.status == 'in_progress':
                ce.status = 'completed'
                ce.is_passed = True
                updates.append(ce)
    return updates


def _complete_graduated_enrolment_ces(enrolments, ces_by_enrolment: dict) -> list:
    """
    When an enrolment is marked graduated (either from the CSV is_program_graduated
    or derived), all its CourseEnrolments must be completed. Handles the gap where
    is_program_graduated=Yes but is_course_graduated=No on the final course.
    Mutates CE objects in-place; returns the updated objects for bulk_update.
    """
    updates = []
    for enrolment in enrolments:
        if not enrolment.is_graduated:
            continue
        for ce in ces_by_enrolment.get(enrolment.pk, []):
            if ce.status != 'completed':
                ce.status = 'completed'
                ce.is_passed = True
                updates.append(ce)
    return updates


_HEALTH_PRIORITY = {
    'dormant': 0, 'at_risk': 1, 'active': 2, 'not_yet_started': 3, 'graduated': 4,
}


def _update_learner_health_rollups(enrolments):
    from selfpaced.models import Learner, PaymentStatus

    learner_enrolments: dict = defaultdict(list)
    for e in enrolments:
        if not e.programme.is_prerequisite:
            learner_enrolments[e.learner_id].append(e)

    payment_by_learner = {}
    for _chunk in _chunked(list(learner_enrolments.keys())):
        payment_by_learner.update(
            Learner.objects.filter(email__in=_chunk).values_list('email', 'payment_status')
        )

    _payment_issue_statuses = {
        PaymentStatus.DUE_SOON, PaymentStatus.GRACE_PERIOD, PaymentStatus.OVERDUE,
    }

    updates = []
    for learner_id, e_list in learner_enrolments.items():
        if not e_list:
            continue
        # Active-wins: a learner is only at-risk / dormant if they have NO active enrolment.
        # Graduated only when every enrolment is graduated.
        non_graduated = [e for e in e_list if e.health_status != 'graduated']
        if not non_graduated:
            overall = 'graduated'
        else:
            statuses = {e.health_status for e in non_graduated}
            if 'active' in statuses:
                overall = 'active'
            elif 'at_risk' in statuses:
                overall = 'at_risk'
            elif 'dormant' in statuses:
                overall = 'dormant'
            else:
                overall = 'not_yet_started'
        updates.append(Learner(email=learner_id, overall_health_status=overall))

    if updates:
        Learner.objects.bulk_update(updates, ['overall_health_status'], batch_size=500)

    # Learners whose only enrolments are prerequisite programmes (e.g. WALX-only)
    # were never added to learner_enrolments; reset any stale 'graduated' status.
    all_processed = {e.learner_id for e in enrolments}
    substantive   = set(learner_enrolments.keys())
    prereq_only   = all_processed - substantive
    if prereq_only:
        Learner.objects.filter(
            email__in=prereq_only,
        ).exclude(
            overall_health_status='not_yet_started',
        ).update(overall_health_status='not_yet_started')


def _create_snapshot(enrolment, job, upload_date: date) -> None:
    from selfpaced.models import AssignmentProgress, CourseEnrolment, EnrolmentSnapshot

    ces = CourseEnrolment.objects.filter(enrolment=enrolment)
    ap_qs = AssignmentProgress.objects.filter(course_enrolment__in=ces)

    courses_completed = ces.filter(status='completed').count()
    assignments_accessed = ap_qs.filter(is_accessed=True).count()
    assignments_submitted = ap_qs.filter(is_submitted=True).count()
    assignments_passed = ap_qs.filter(is_passed=True).count()
    pass_rate = (
        assignments_passed / assignments_submitted * 100
        if assignments_submitted > 0 else None
    )

    activity_dates = (
        list(ap_qs.exclude(accessed_date=None).values_list('accessed_date', flat=True))
        + list(ap_qs.exclude(submitted_date=None).values_list('submitted_date', flat=True))
    )
    last_activity = max(activity_dates) if activity_dates else None
    days_since_activity = (upload_date - last_activity).days if last_activity else None

    fsol = enrolment.first_sign_of_life_date
    days_since_fsol = (upload_date - fsol).days if fsol else None

    EnrolmentSnapshot.objects.create(
        learner_id=enrolment.learner_id,
        enrolment=enrolment,
        programme=enrolment.programme,
        ingestion_job=job,
        snapshot_date=upload_date,
        current_course_sequence=(
            enrolment.current_course.sequence_number
            if enrolment.current_course_id else None
        ),
        courses_completed=courses_completed,
        assignments_accessed=assignments_accessed,
        assignments_submitted=assignments_submitted,
        assignments_passed=assignments_passed,
        pass_rate=pass_rate,
        last_activity_date=last_activity,
        days_since_last_activity=days_since_activity,
        days_since_first_sign_of_life=days_since_fsol,
        health_status=enrolment.health_status,
        active_flags=enrolment.active_flags,
        payment_status=enrolment.learner.payment_status,
    )


# ---------------------------------------------------------------------------
# Recompute health — shared logic used by both the management command and
# the admin UI button.  No CSV re-upload; recalculates from stored data.
# ---------------------------------------------------------------------------

def recompute_health(
    programme_code: str | None = None,
    job_pk: int | None = None,
) -> dict:
    """
    Recompute health flags for all (or a filtered subset of) enrolments using
    today's date and current threshold configuration.

    Filters (mutually exclusive, programme_code takes priority):
      programme_code — recompute only enrolments in this programme
      job_pk         — recompute only enrolments that have a snapshot for this job

    Returns a summary dict: {'updated': int, 'errors': int, 'error_detail': list}.
    """
    from selfpaced.models import AssignmentProgress, Course, CourseEnrolment, Enrolment, EnrolmentSnapshot, FlagCode, Learner, PaymentStatus, ProgrammeThreshold

    _rc_payment_issue_statuses = {PaymentStatus.DUE_SOON, PaymentStatus.GRACE_PERIOD, PaymentStatus.OVERDUE}
    today = date.today()

    _recompute_state['running'] = True
    _recompute_state['total'] = 0
    _recompute_state['done'] = 0
    _recompute_state['errors'] = 0
    if not _recompute_state.get('started_at'):
        _recompute_state['started_at'] = datetime.now().strftime('%H:%M:%S')
    _recompute_state['finished_at'] = None

    qs = Enrolment.objects.select_related('learner', 'programme').all()
    if programme_code:
        qs = qs.filter(programme__code=programme_code.upper())
    elif job_pk:
        enrolment_pks = EnrolmentSnapshot.objects.filter(
            ingestion_job_id=job_pk
        ).values_list('enrolment_id', flat=True).distinct()
        qs = qs.filter(pk__in=enrolment_pks)
    enrolments = list(qs)
    _recompute_state['total'] = len(enrolments)

    ces_qs = CourseEnrolment.objects.filter(enrolment__in=enrolments).select_related('course')
    ces_by_enrolment: dict = defaultdict(list)
    for ce in ces_qs:
        ces_by_enrolment[ce.enrolment_id].append(ce)

    _infer_updates = _infer_course_completions(ces_by_enrolment)
    if _infer_updates:
        CourseEnrolment.objects.bulk_update(_infer_updates, ['status', 'is_passed'], batch_size=500)

    _grad_ce_updates = _complete_graduated_enrolment_ces(enrolments, ces_by_enrolment)
    if _grad_ce_updates:
        CourseEnrolment.objects.bulk_update(_grad_ce_updates, ['status', 'is_passed'], batch_size=500)

    aps_by_ce: dict = defaultdict(list)
    for ap in (
        AssignmentProgress.objects
        .filter(course_enrolment__in=ces_qs)
        .select_related('assignment')
    ):
        aps_by_ce[ap.course_enrolment_id].append(ap)

    prog_course_counts: dict = {
        row['programme_id']: row['n']
        for row in Course.objects.filter(is_active=True)
        .values('programme_id')
        .annotate(n=Count('id'))
    }

    threshold_cache: dict = {}
    errors = []
    updated_enrolments: list = []

    # Pre-fetch learner payment statuses in bulk (needed for payment flag)
    all_learner_pks = {e.learner_id for e in enrolments}
    learner_payment: dict = {}
    for _chunk in _chunked(list(all_learner_pks)):
        learner_payment.update(
            Learner.objects.filter(email__in=_chunk).values_list('email', 'payment_status')
        )

    for i, enrolment in enumerate(enrolments):
        _recompute_state['done'] = i + 1
        ces = ces_by_enrolment.get(enrolment.pk, [])
        aps: list = []
        for ce in ces:
            aps.extend(aps_by_ce.get(ce.pk, []))
        if enrolment.programme_id not in threshold_cache:
            threshold_cache[enrolment.programme_id] = ProgrammeThreshold.for_programme(enrolment.programme)
        try:
            health_status, active_flags, flag_detail = compute_enrolment_health(
                enrolment, today,
                prefetched_course_enrolments=ces,
                prefetched_all_progress=aps,
                prefetched_threshold=threshold_cache[enrolment.programme_id],
                programme_course_count=prog_course_counts.get(enrolment.programme_id),
            )
            if (learner_payment.get(enrolment.learner_id) in _rc_payment_issue_statuses
                    and FlagCode.PAYMENT_ISSUE not in active_flags):
                active_flags = list(active_flags) + [FlagCode.PAYMENT_ISSUE]
            enrolment.health_status = health_status
            enrolment.active_flags = active_flags
            enrolment.flag_detail = flag_detail
            updated_enrolments.append(enrolment)
        except Exception as exc:
            logger.exception('Health recompute failed for enrolment %s: %s', enrolment.pk, exc)
            errors.append(f'{enrolment.learner_id} ({enrolment.programme.code}): {exc}')
            _recompute_state['errors'] = len(errors)

    # Single bulk_update instead of one UPDATE per enrolment
    if updated_enrolments:
        Enrolment.objects.bulk_update(
            updated_enrolments,
            ['health_status', 'active_flags', 'flag_detail'],
            batch_size=500,
        )

    _update_learner_health_rollups(enrolments)

    _recompute_state['running'] = False
    _recompute_state['finished_at'] = datetime.now().strftime('%H:%M:%S')
    _recompute_state['errors'] = len(errors)

    return {'updated': len(enrolments), 'errors': len(errors), 'error_detail': errors}
