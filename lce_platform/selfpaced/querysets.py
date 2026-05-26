from django.db.models import Count, F, Q

from selfpaced.models import Learner


def real_learners_qs():
    """Learner queryset excluding those whose only enrolments are in prerequisite programmes."""
    walx_only = (
        Learner.objects
        .annotate(
            _total=Count('enrolments', distinct=True),
            _walx=Count('enrolments', filter=Q(enrolments__programme__is_prerequisite=True), distinct=True),
        )
        .filter(_total__gt=0, _total=F('_walx'))
        .values_list('email', flat=True)
    )
    return Learner.objects.exclude(email__in=walx_only)


def activity_learners_qs():
    """
    Learner queryset for metric denominators — real learners who have appeared in
    at least one activity CSV upload (has_activity_data=True on at least one
    non-prerequisite enrolment).

    Excludes:
      - WALX-only learners (same as real_learners_qs)
      - Learners who exist only from the enrollment/roster CSV upload and have
        never been seen in an eHub activity export
    """
    walx_only = (
        Learner.objects
        .annotate(
            _total=Count('enrolments', distinct=True),
            _walx=Count('enrolments', filter=Q(enrolments__programme__is_prerequisite=True), distinct=True),
        )
        .filter(_total__gt=0, _total=F('_walx'))
        .values_list('email', flat=True)
    )
    activity_emails = (
        Learner.objects
        .filter(enrolments__has_activity_data=True, enrolments__programme__is_prerequisite=False)
        .values_list('email', flat=True)
        .distinct()
    )
    return Learner.objects.filter(email__in=activity_emails).exclude(email__in=walx_only)
