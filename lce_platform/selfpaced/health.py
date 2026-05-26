"""
Health flag computation engine for the self-paced platform.

Pure functions — no database writes. Takes model instances and a reference
date (the upload date), returns (health_status, active_flags, flag_detail).

Flag codes (from models.FlagCode):
  never_activated         — has FSOL but no assignment accessed within threshold
  inactive                — previously active but no activity within threshold
  stuck_on_assignment     — accessed but not submitted within threshold
  low_pass_rate           — pass rate below threshold
  stalled_between_courses — course complete but next not started within threshold
  payment_issue           — payment_status != compliant
"""

from datetime import date

from selfpaced.models import FlagCode, HealthStatus, PaymentStatus, ProgrammeThreshold


# ---------------------------------------------------------------------------
# Individual flag functions
# ---------------------------------------------------------------------------

def flag_never_activated(enrolment, all_progress, upload_date: date, threshold: ProgrammeThreshold) -> bool:
    """
    Fires when: learner has a first_sign_of_life_date but has never accessed
    any assignment, and days since FSOL > activation_threshold_days.
    """
    fsol = enrolment.first_sign_of_life_date
    if not fsol:
        return False
    if any(p.is_accessed for p in all_progress):
        return False
    days = (upload_date - fsol).days
    return days > threshold.get('activation_threshold_days')


def flag_inactive(last_activity_date, upload_date: date, threshold: ProgrammeThreshold) -> bool:
    """
    Fires when: learner has prior activity but days since last activity
    > inactivity_threshold_days.
    """
    if not last_activity_date:
        return False
    days = (upload_date - last_activity_date).days
    return days > threshold.get('inactivity_threshold_days')


def flag_stuck_on_assignment(all_progress, upload_date: date, threshold: ProgrammeThreshold) -> dict | None:
    """
    Fires when: an assignment has been accessed but not submitted and
    days since accessed_date > stuck_assignment_threshold_days.
    Returns detail dict {'assignment': name, 'days': n} or None.
    """
    limit = threshold.get('stuck_assignment_threshold_days')
    for p in all_progress:
        if p.is_accessed and not p.is_submitted and p.accessed_date:
            days = (upload_date - p.accessed_date).days
            if days > limit:
                return {
                    'assignment': p.assignment.name,
                    'days': days,
                    'accessed_date': p.accessed_date.isoformat(),
                }
    return None


def flag_low_pass_rate(all_progress, threshold: ProgrammeThreshold) -> bool:
    """
    Fires when: submitted count > 0 and pass_rate < pass_rate_threshold_pct.
    """
    submitted = [p for p in all_progress if p.is_submitted]
    if not submitted:
        return False
    passed = sum(1 for p in submitted if p.is_passed)
    rate = (passed / len(submitted)) * 100
    return rate < threshold.get('pass_rate_threshold_pct')


def flag_stalled_between_courses(course_enrolments, upload_date: date, threshold: ProgrammeThreshold) -> dict | None:
    """
    Fires when: a course is completed but the next course (by sequence_number)
    has status not_started and days since completion > inter_course_threshold_days.
    Returns detail dict {'completed_course': name, 'days': n} or None.
    """
    limit = threshold.get('inter_course_threshold_days')
    sorted_ces = sorted(course_enrolments, key=lambda ce: ce.course.sequence_number)
    for i, ce in enumerate(sorted_ces[:-1]):
        if ce.status == 'completed' and ce.completion_date:
            next_ce = sorted_ces[i + 1]
            if next_ce.status == 'not_started':
                days = (upload_date - ce.completion_date).days
                if days > limit:
                    return {
                        'completed_course': ce.course.full_name,
                        'next_course': next_ce.course.full_name,
                        'days': days,
                    }
    return None


def flag_stalled_progression(
    enrolment,
    course_enrolments,
    upload_date: date,
    threshold: ProgrammeThreshold,
    learner_active_enrolment_pks: dict | None = None,
) -> dict | None:
    """
    Fires when a learner completed the last known course in this enrolment
    but has no active or completed course work in any OTHER programme.

    Pass learner_active_enrolment_pks (learner_id → set of enrolment_pks with
    in_progress/completed CEs) to avoid a per-enrolment DB query. When omitted,
    falls back to a live query (safe for standalone use, slow in bulk).
    """
    if not course_enrolments:
        return None

    sorted_ces = sorted(course_enrolments, key=lambda ce: ce.course.sequence_number)
    highest = sorted_ces[-1]

    if highest.status != 'completed' or not highest.completion_date:
        return None

    if learner_active_enrolment_pks is not None:
        active_for_learner = learner_active_enrolment_pks.get(enrolment.learner_id, set())
        has_other_activity = bool(active_for_learner - {enrolment.pk})
    else:
        from selfpaced.models import CourseEnrolment as CE
        has_other_activity = CE.objects.filter(
            enrolment__learner=enrolment.learner,
            status__in=['in_progress', 'completed'],
        ).exclude(enrolment=enrolment).exists()

    if has_other_activity:
        return None

    days = (upload_date - highest.completion_date).days
    if days > threshold.get('inter_course_threshold_days'):
        return {
            'completed_course': highest.course.full_name,
            'completed_seq': highest.course.sequence_number,
            'programme': enrolment.programme.code,
            'days_since_completion': days,
        }
    return None


def flag_payment_issue(learner) -> str | None:
    """
    Fires when: payment_status is not compliant.
    Returns the payment_status value or None.
    """
    if learner.payment_status != PaymentStatus.COMPLIANT:
        return learner.payment_status
    return None


# ---------------------------------------------------------------------------
# Health status rollup
# ---------------------------------------------------------------------------

def compute_health_status(
    active_flags: list[str],
    days_since_last_activity: int | None,
    days_since_fsol: int | None,
    threshold: ProgrammeThreshold,
    is_graduated: bool,
    has_fsol: bool,
) -> str:
    if is_graduated:
        return HealthStatus.GRADUATED
    if not has_fsol:
        return HealthStatus.NOT_YET_STARTED
    dormancy = threshold.get('dormancy_threshold_days')
    # Dormant via inactivity
    if days_since_last_activity is not None and days_since_last_activity > dormancy:
        return HealthStatus.DORMANT
    # Dormant via never-activated: has FSOL but zero activity and beyond dormancy threshold
    if (FlagCode.NEVER_ACTIVATED in active_flags
            and days_since_fsol is not None
            and days_since_fsol > dormancy):
        return HealthStatus.DORMANT
    if active_flags:
        return HealthStatus.AT_RISK
    return HealthStatus.ACTIVE


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_enrolment_health(
    enrolment,
    upload_date: date,
    *,
    prefetched_course_enrolments=None,
    prefetched_all_progress=None,
    prefetched_threshold=None,
    programme_course_count: int | None = None,
    learner_active_enrolment_pks: dict | None = None,
) -> tuple[str, list, dict]:
    """
    Compute health flags and status for a single enrolment.

    Pass prefetched_* kwargs when calling in bulk to avoid N+1 queries.
    learner_active_enrolment_pks: {learner_id: set of enrolment_pks with active CEs}
      — eliminates the per-enrolment DB query in flag_stalled_progression.

    Returns:
        (health_status_str, active_flag_codes, flag_detail_dict)
    """
    from selfpaced.models import AssignmentProgress, CourseEnrolment

    threshold = prefetched_threshold or ProgrammeThreshold.for_programme(enrolment.programme)

    if prefetched_course_enrolments is not None:
        course_enrolments = prefetched_course_enrolments
    else:
        course_enrolments = list(
            CourseEnrolment.objects.filter(enrolment=enrolment)
            .select_related('course')
        )

    if prefetched_all_progress is not None:
        all_progress = prefetched_all_progress
    else:
        all_progress = list(
            AssignmentProgress.objects.filter(course_enrolment__in=course_enrolments)
            .select_related('assignment')
        )

    # Last activity date across all assignment records
    activity_dates = [
        d for p in all_progress
        for d in (p.accessed_date, p.submitted_date)
        if d
    ]
    last_activity = max(activity_dates) if activity_dates else None
    days_since_activity = (upload_date - last_activity).days if last_activity else None

    # FSOL: use explicit date, then activity dates, then any submission/pass evidence.
    # Prerequisite programme CSVs often omit date columns — the evidence fallback prevents
    # learners with passed assignments from showing as "Not Started".
    effective_fsol = enrolment.first_sign_of_life_date or last_activity
    has_activity_evidence = any(
        p.is_accessed or p.is_submitted or p.is_passed for p in all_progress
    )
    has_fsol = bool(effective_fsol) or has_activity_evidence
    days_since_fsol = (upload_date - effective_fsol).days if effective_fsol else None

    active_flags = []
    flag_detail = {}

    # Never activated
    if flag_never_activated(enrolment, all_progress, upload_date, threshold):
        active_flags.append(FlagCode.NEVER_ACTIVATED)
        if days_since_fsol is not None:
            flag_detail[FlagCode.NEVER_ACTIVATED] = {'days': days_since_fsol}

    # Inactive (only if they have been active before)
    if last_activity and flag_inactive(last_activity, upload_date, threshold):
        active_flags.append(FlagCode.INACTIVE)
        if days_since_activity is not None:
            flag_detail[FlagCode.INACTIVE] = {'days': days_since_activity}

    # Stuck on assignment
    stuck = flag_stuck_on_assignment(all_progress, upload_date, threshold)
    if stuck:
        active_flags.append(FlagCode.STUCK_ON_ASSIGNMENT)
        flag_detail[FlagCode.STUCK_ON_ASSIGNMENT] = stuck

    # Low pass rate
    if flag_low_pass_rate(all_progress, threshold):
        active_flags.append(FlagCode.LOW_PASS_RATE)
        submitted_aps = [p for p in all_progress if p.is_submitted]
        if submitted_aps:
            passed_count = sum(1 for p in submitted_aps if p.is_passed)
            flag_detail[FlagCode.LOW_PASS_RATE] = {
                'rate': round(passed_count / len(submitted_aps) * 100),
                'passed': passed_count,
                'submitted': len(submitted_aps),
            }

    # Stalled between courses (within same programme)
    stalled = flag_stalled_between_courses(course_enrolments, upload_date, threshold)
    if stalled:
        active_flags.append(FlagCode.STALLED_BETWEEN_COURSES)
        flag_detail[FlagCode.STALLED_BETWEEN_COURSES] = stalled

    # Stalled progression (cross-programme — completed last known course, no activity elsewhere)
    stalled_prog = flag_stalled_progression(
        enrolment, course_enrolments, upload_date, threshold,
        learner_active_enrolment_pks=learner_active_enrolment_pks,
    )
    if stalled_prog:
        active_flags.append(FlagCode.STALLED_PROGRESSION)
        flag_detail[FlagCode.STALLED_PROGRESSION] = stalled_prog

    # Payment override is applied by the engine after this function returns:
    # forces health_status to at_risk + adds payment_issue flag per enrolment,
    # then rolls up to Learner.overall_health_status.

    # Derive graduation from course completions in case the stored flag lags behind.
    # Require that the number of completed enrolments meets the total programme course
    # count — prevents marking graduated when only a subset of courses is uploaded.
    # Use is_passed as well as status='completed' — prerequisite programmes (e.g. WALX)
    # may have is_passed=True before status is flipped to 'completed' in the pipeline.
    all_enrolled_completed = bool(course_enrolments) and all(
        ce.status == 'completed' or ce.is_passed for ce in course_enrolments
    )
    meets_course_count = (
        programme_course_count is None
        or len(course_enrolments) >= programme_course_count
    )
    is_graduated = enrolment.is_graduated or (all_enrolled_completed and meets_course_count)

    # A graduated enrolment should not carry warning flags — clear them so the UI
    # doesn't show "Inactive" or "At risk" alongside a Graduated badge.
    if is_graduated:
        active_flags = []
        flag_detail = {}

    health_status = compute_health_status(
        active_flags=active_flags,
        days_since_last_activity=days_since_activity,
        days_since_fsol=days_since_fsol,
        threshold=threshold,
        is_graduated=is_graduated,
        has_fsol=has_fsol,
    )

    return health_status, active_flags, flag_detail
