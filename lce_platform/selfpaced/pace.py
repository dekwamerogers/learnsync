from datetime import date, timedelta

from django.utils import timezone

GRACE_DAYS = 14  # new learners with zero completions aren't penalised in their first 2 weeks


def compute_pod_pace(pod_assignment, as_of=None):
    """
    Compute and save pace metrics for a single PodAssignment.

    Unit: courses per week (c/week) — weekly rhythm is more intuitive than per-day
    for self-paced programmes where courses take 1-3 weeks each.

    current_pace   = courses_completed / weeks_active
    required_pace  = courses_remaining / weeks_remaining
    pace_status    = comparison of current vs required (ratio-based)
    courses_behind = how many courses behind the expected trajectory
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

    courses_completed = CourseEnrolment.objects.filter(
        enrolment=enrolment,
        status=CourseStatus.COMPLETED,
    ).count()

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
    courses_remaining = max(0, total_courses - courses_completed)

    # Effective start date — when the learner's pace clock should begin:
    #
    # If enrolment_date is on or after programme.start_date, the learner joined
    # mid-programme and their clock starts from enrolment_date.
    #
    # If enrolment_date is before programme.start_date (enrolled early), they
    # couldn't do anything yet, so use first_sign_of_life_date (actual first
    # engagement with this programme's courses) as the learner-side date,
    # falling back to the pod assignment date, then the enrolment_date itself.
    #
    # Either way, floor to programme.start_date so pre-launch enrolees aren't
    # penalised for days before the programme existed.
    prog_start     = programme.start_date
    enrolment_date = enrolment.enrolment_date
    fsol           = enrolment.first_sign_of_life_date

    if enrolment_date and (prog_start is None or enrolment_date >= prog_start):
        learner_date = enrolment_date
    else:
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

    if courses_remaining == 0:
        required_pace = 0.0
    elif weeks_remaining > 0:
        required_pace = courses_remaining / weeks_remaining  # c/week
    else:
        required_pace = None  # past target date

    # Projected completion date
    if courses_remaining == 0:
        projected = today
    elif current_pace > 0:
        projected = today + timedelta(weeks=courses_remaining / current_pace)
    else:
        projected = None

    # Pace status
    threshold = ProgrammeThreshold.for_programme(programme)
    behind_pct = threshold.get('pod_behind_threshold_pct')

    if courses_remaining == 0:
        pace_status = PaceStatus.COMPLETED
    elif required_pace is None:
        # Past the target date and still not done
        pace_status = PaceStatus.SIGNIFICANTLY_BEHIND
    elif current_pace == 0:
        # Grace period: don't penalise new learners who haven't finished a course yet
        if days_active <= GRACE_DAYS:
            pace_status = PaceStatus.ON_TRACK
        else:
            pace_status = PaceStatus.SIGNIFICANTLY_BEHIND
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
        # they have already begun.  In-progress courses are accounted for, so only
        # courses that haven't been touched yet form the ceiling.
        # (e.g. someone on the final course in progress → cap = 0, never "X courses behind")
        courses_not_started = max(0.0, float(total_courses - courses_completed - courses_in_progress))
        courses_behind = min(raw_behind, courses_not_started)
    else:
        courses_behind = 0.0

    pod_assignment.current_pace = current_pace
    pod_assignment.required_pace = required_pace
    pod_assignment.pace_status = pace_status
    pod_assignment.courses_behind = round(courses_behind, 2)
    pod_assignment.projected_completion_date = projected
    pod_assignment.last_computed_at = timezone.now()
    pod_assignment.save(update_fields=[
        'current_pace', 'required_pace', 'pace_status',
        'courses_behind', 'projected_completion_date', 'last_computed_at',
    ])


def compute_pod_paces(pod):
    """Recompute pace for all current assignments in a pod."""
    for assignment in pod.assignments.filter(is_current=True).select_related(
        'learner', 'programme', 'pod'
    ):
        compute_pod_pace(assignment)
