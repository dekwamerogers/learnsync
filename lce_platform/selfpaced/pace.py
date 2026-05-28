from datetime import date, timedelta

from django.utils import timezone

GRACE_DAYS = 14  # new learners with zero completions aren't penalised in their first 2 weeks


def compute_pod_pace(pod_assignment, as_of=None):
    """
    Compute and save pace metrics for a single PodAssignment.

    Unit: courses per week (c/week) — weekly rhythm is more intuitive than per-day
    for self-paced programmes where courses take 1-3 weeks each.

    current_pace   = courses_completed / weeks_active
    required_pace  = courses_remaining_effective / weeks_remaining
    pace_status    = comparison of current vs required (ratio-based)
    courses_behind = how many courses behind the expected trajectory

    Key design: two flavours of "courses remaining" are used:

        courses_remaining_total     = total - completed
            Used ONLY for the COMPLETED check (all courses done → graduated).

        courses_remaining_effective = total - completed - in_progress
            Used for required_pace, projected completion, and courses_behind.
            In-progress courses are treated as "already in flight" — the learner
            is actively working on them, so they don't add to the future burden.
            This prevents a learner on their last course from appearing Behind,
            and gives more accurate projections for those near the finish line.
    """
    from selfpaced.models import (
        CourseEnrolment,
        CourseStatus,
        Enrolment,
        PaceStatus,
        ProgrammeThreshold,
    )

    today = as_of or date.today()
    learner = pod_assignment.learner
    programme = pod_assignment.programme
    pod = pod_assignment.pod

    try:
        enrolment = Enrolment.objects.get(learner=learner, programme=programme)
    except Enrolment.DoesNotExist:
        return

    # Fetch all completed course enrolments in one query — needed for both
    # counts and learning velocity (which requires individual completion dates).
    completed_ces = list(
        CourseEnrolment.objects
        .filter(enrolment=enrolment, status=CourseStatus.COMPLETED)
        .values_list('completion_date', flat=True)
        .order_by('completion_date')
    )
    courses_completed = len(completed_ces)

    courses_in_progress = CourseEnrolment.objects.filter(
        enrolment=enrolment,
        status=CourseStatus.IN_PROGRESS,
    ).count()

    total_courses = (
        programme.total_courses_for_graduation
        # Fallback: count active, non-prerequisite courses (exclude WALX which lives
        # on its own standalone enrolment and must not inflate the target count)
        or programme.courses.filter(is_active=True).exclude(code='WALX').count()
    )

    # courses_remaining_total: used only for the COMPLETED check (all courses done).
    courses_remaining_total = max(0, total_courses - courses_completed)

    # courses_remaining_effective: used for pace, projection, and courses_behind.
    # Subtracts in-progress courses because they are already being worked on —
    # they should not inflate the required pace or push the projected date outward.
    # Example: 3 completed, 1 in-progress (AICE-4), total=6 → effective=2 (AICE-5, 6).
    # Without this, the learner's required pace would include AICE-4 as if untouched,
    # making them appear Behind when they are in fact on track.
    courses_remaining_effective = max(0, total_courses - courses_completed - courses_in_progress)

    # Effective start date — when the learner's pace clock should begin.
    #
    # We always prefer first_sign_of_life_date (FSOL): the date the learner
    # first appeared in eHub for this programme.  This is more accurate than
    # enrolment_date because a learner may be enrolled weeks before they
    # actually begin, and penalising them for that administrative lag distorts
    # their pace downward.
    #
    # Falls back to enrolment_date (if FSOL is absent), then assignment_date.
    # Floored to programme.start_date so pre-launch enrolees aren't penalised
    # for days before the programme existed.
    prog_start     = programme.start_date
    enrolment_date = enrolment.enrolment_date
    fsol           = enrolment.first_sign_of_life_date

    learner_date = fsol or enrolment_date or pod_assignment.assignment_date

    if not learner_date and not prog_start:
        return  # no date reference at all — skip

    if learner_date and prog_start:
        start_date = max(learner_date, prog_start)
    else:
        start_date = learner_date or prog_start

    days_active = max(1, (today - start_date).days)
    weeks_active = days_active / 7

    days_remaining = (pod.target_month - today).days
    weeks_remaining = days_remaining / 7

    current_pace = courses_completed / weeks_active  # c/week

    # Required pace: how fast must the learner go to finish the REMAINING (unstarted) courses
    # by the pod target month?  Uses courses_remaining_effective so in-progress courses
    # don't inflate the target — they're already being worked on.
    if courses_remaining_total == 0:
        required_pace = 0.0
    elif weeks_remaining > 0:
        required_pace = courses_remaining_effective / weeks_remaining  # c/week
    else:
        required_pace = None  # past target date — any outstanding work is overdue

    # Projected completion date — also based on effective remaining so in-progress
    # courses don't artificially extend the projection.
    # Edge case: if effective remaining = 0 but total > 0 (on the very last course),
    # project a half-course of work to signal "nearly done" rather than "today".
    if courses_remaining_total == 0:
        projected = today
    elif current_pace > 0:
        remaining_for_projection = (
            courses_remaining_effective
            if courses_remaining_effective > 0
            else 0.5 * courses_in_progress   # last course(s) in progress — assume ~halfway done
        )
        projected = today + timedelta(weeks=remaining_for_projection / current_pace)
    else:
        projected = None

    # Pace status
    threshold = ProgrammeThreshold.for_programme(programme)
    behind_pct = threshold.get('pod_behind_threshold_pct')

    if courses_remaining_total == 0:
        # All courses completed — graduated.
        pace_status = PaceStatus.COMPLETED
    elif required_pace is None:
        # Past the target date and still not done.
        pace_status = PaceStatus.SIGNIFICANTLY_BEHIND
    elif current_pace == 0:
        # No courses completed yet.
        # Grace period: don't penalise new learners who haven't finished a course yet.
        if days_active <= GRACE_DAYS:
            pace_status = PaceStatus.ON_TRACK
        else:
            pace_status = PaceStatus.SIGNIFICANTLY_BEHIND
    elif required_pace == 0.0:
        # All remaining courses are in-progress (effective = 0) — finishing up.
        pace_status = PaceStatus.AHEAD
    else:
        ratio = current_pace / required_pace
        if ratio > 1.05:
            pace_status = PaceStatus.AHEAD
        elif ratio >= (1 - behind_pct / 100):
            pace_status = PaceStatus.ON_TRACK
        elif ratio >= 0.6:
            pace_status = PaceStatus.BEHIND
        else:
            pace_status = PaceStatus.SIGNIFICANTLY_BEHIND

    if pace_status in (PaceStatus.BEHIND, PaceStatus.SIGNIFICANTLY_BEHIND):
        expected_by_now = (required_pace or 0) * weeks_active
        raw_behind = max(0.0, expected_by_now - courses_completed)
        # Cap at courses NOT YET STARTED — a learner can't be "behind" on a course
        # they have already begun.  courses_remaining_effective equals courses_not_started
        # so reuse it directly as the cap.
        courses_behind = min(raw_behind, float(courses_remaining_effective))
    else:
        courses_behind = 0.0

    # ── Learning velocity (inter-completion rate) ─────────────────────────
    # Uses individual course completion_date values collected earlier.
    # Requires 2+ completions with known dates; the window is
    # first_completion → last_completion, measuring the rhythm between courses
    # independently of current dormancy or pre-engagement idle time.
    #
    # Formula: (completions - 1) / weeks(first_completion, last_completion)
    # Why (n-1): with n completions there are (n-1) inter-completion intervals.
    #
    # Example: completed AICE-1 on 1 Jan, AICE-2 on 15 Jan, AICE-3 on 5 Feb
    #   → window = 35 days = 5 weeks, intervals = 2
    #   → learning_velocity = 2 / 5 = 0.4 c/week
    #
    # This tells coaches: "when actively learning, this person moves at X c/week"
    # — distinct from current_pace which includes idle stretches since FSOL.
    learning_velocity = None
    dated_completions = [d for d in completed_ces if d is not None]
    if len(dated_completions) >= 2:
        first_c = dated_completions[0]
        last_c  = dated_completions[-1]
        span_weeks = (last_c - first_c).days / 7
        if span_weeks > 0:
            learning_velocity = (len(dated_completions) - 1) / span_weeks

    pod_assignment.current_pace = current_pace
    pod_assignment.required_pace = required_pace
    pod_assignment.pace_status = pace_status
    pod_assignment.courses_behind = round(courses_behind, 2)
    pod_assignment.projected_completion_date = projected
    pod_assignment.learning_velocity = round(learning_velocity, 3) if learning_velocity is not None else None
    pod_assignment.last_computed_at = timezone.now()
    pod_assignment.save(update_fields=[
        'current_pace', 'required_pace', 'pace_status',
        'courses_behind', 'projected_completion_date', 'learning_velocity',
        'last_computed_at',
    ])


def compute_pod_paces(pod):
    """Recompute pace for all current assignments in a pod."""
    for assignment in pod.assignments.filter(is_current=True).select_related(
        'learner', 'programme', 'pod'
    ):
        compute_pod_pace(assignment)
