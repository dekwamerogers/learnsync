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
