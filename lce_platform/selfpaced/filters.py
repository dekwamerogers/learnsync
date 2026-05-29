from datetime import date, timedelta

import django_filters
from django.db.models import Q

from selfpaced.models import Assignment, Course, HealthStatus, Learner, PaymentStatus, Programme


class HealthFilter(django_filters.MultipleChoiceFilter):
    """Extends the standard health status filter with an 'Onboarded' pseudo-status."""

    def filter(self, qs, value):
        if not value:
            return qs
        regular = [v for v in value if v != 'onboarded']
        q = Q()
        if regular:
            q |= Q(overall_health_status__in=regular)
        if 'onboarded' in value:
            q |= Q(
                enrolments__programme__is_prerequisite=True,
                enrolments__is_graduated=True,
            )
        return qs.filter(q).distinct() if q else qs


def _active_programme_qs():
    today = date.today()
    return Programme.objects.filter(
        is_active=True,
        is_prerequisite=False,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    ).order_by('code')


class LearnerFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label='Search')
    programme = django_filters.ModelMultipleChoiceFilter(
        queryset=_active_programme_qs(),
        field_name='enrolments__programme',
        label='Programme',
    )
    health = HealthFilter(
        choices=[
            *HealthStatus.choices,
            ('onboarded', 'Onboarded'),
        ],
        label='Health',
    )
    payment = django_filters.MultipleChoiceFilter(
        choices=PaymentStatus.choices,
        field_name='payment_status',
        label='Payment',
    )
    country = django_filters.MultipleChoiceFilter(
        field_name='country',
        label='Country',
        choices=[],
    )
    course = django_filters.ModelMultipleChoiceFilter(
        queryset=Course.objects.filter(is_active=True)
                               .select_related('programme')
                               .order_by('programme__code', 'sequence_number'),
        method='filter_course',
        label='Course / Module',
    )
    course_status = django_filters.MultipleChoiceFilter(
        choices=[
            ('in_progress', 'In Progress'),
            ('completed',   'Completed'),
            ('not_started', 'Not Started'),
            ('withdrawn',   'Withdrawn'),
        ],
        method='filter_course_status',
        label='Course Status',
    )
    assignment = django_filters.ModelMultipleChoiceFilter(
        queryset=Assignment.objects.filter(is_active=True)
                                   .select_related('course__programme')
                                   .order_by('course__programme__code', 'course__sequence_number', 'sequence_in_course'),
        field_name='enrolments__course_enrolments__assignment_progress__assignment',
        label='Assignment',
    )
    graduated = django_filters.ChoiceFilter(
        choices=[
            ('badge', 'Has a badge (completed ≥1 course)'),
            ('certificate', 'Has a certificate (graduated programme)'),
        ],
        method='filter_graduated',
        label='Graduation',
        empty_label='Any graduation status',
    )
    since = django_filters.ChoiceFilter(
        choices=[
            ('7',   'Last 7 days'),
            ('30',  'Last 30 days'),
            ('90',  'Last 90 days'),
            ('365', 'Last 12 months'),
        ],
        method='filter_since',
        label='Enrolled since',
        empty_label='All time',
    )
    enrolment_health = django_filters.MultipleChoiceFilter(
        choices=HealthStatus.choices,
        method='filter_enrolment_health',
        label='Enrolment Health',
    )
    flag = django_filters.MultipleChoiceFilter(
        choices=[
            ('inactive',                'Inactive'),
            ('never_activated',         'Never Activated'),
            ('stuck_on_assignment',     'Stuck on Assignment'),
            ('low_pass_rate',           'Low Pass Rate'),
            ('stalled_between_courses', 'Stalled Between Courses'),
            ('stalled_progression',     'No Onward Progress'),
            ('payment_issue',           'Payment Issue'),
        ],
        method='filter_flag',
        label='Flag',
    )
    juggler = django_filters.ChoiceFilter(
        choices=[('1', 'Multi-programme only')],
        method='filter_juggler',
        label='Multi-programme',
        empty_label='',
    )
    follow_up = django_filters.ChoiceFilter(
        choices=[('due', 'Follow-up due')],
        method='filter_follow_up',
        label='Follow-up',
        empty_label='',
    )

    class Meta:
        model = Learner
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        countries = (
            Learner.objects.exclude(country='')
            .values_list('country', flat=True)
            .distinct()
            .order_by('country')
        )
        self.filters['country'].field.choices = [(c, c) for c in countries]

    def search(self, queryset, name, value):
        return queryset.filter(
            Q(email__icontains=value)
            | Q(first_name__icontains=value)
            | Q(last_name__icontains=value)
        )

    def filter_course(self, queryset, name, value):
        """
        Filter by course.  When course_status is also set, apply both
        conditions in a single .filter() span so they must match the *same*
        CourseEnrolment row — prevents the cross-join that chained .filter()
        calls produce on multi-valued relations.
        """
        if not value:
            return queryset
        course_ids = [c.pk for c in value]
        statuses   = [s for s in self.data.getlist('course_status') if s]
        kwargs: dict = {'enrolments__course_enrolments__course_id__in': course_ids}
        if statuses:
            kwargs['enrolments__course_enrolments__status__in'] = statuses
        return queryset.filter(**kwargs).distinct()

    def filter_course_status(self, queryset, name, value):
        """
        Applied standalone only when no course filter is active.
        When a course is selected, filter_course already handles the combined
        condition — we skip here to avoid a second (independent) join.
        """
        if not value:
            return queryset
        if any(pk for pk in self.data.getlist('course') if pk):
            return queryset  # handled by filter_course above
        return queryset.filter(
            enrolments__course_enrolments__status__in=value,
        ).distinct()

    def filter_graduated(self, queryset, name, value):
        if value == 'badge':
            return queryset.filter(
                enrolments__programme__is_prerequisite=False,
                enrolments__course_enrolments__status='completed',
            ).distinct()
        if value == 'certificate':
            return queryset.filter(
                enrolments__programme__is_prerequisite=False,
                enrolments__is_graduated=True,
            ).distinct()
        return queryset

    def filter_health(self, queryset, name, values):
        if not values:
            return queryset
        regular = [v for v in values if v != 'onboarded']
        q = Q()
        if regular:
            q |= Q(overall_health_status__in=regular)
        if 'onboarded' in values:
            q |= Q(
                enrolments__programme__is_prerequisite=True,
                enrolments__is_graduated=True,
            )
        return queryset.filter(q).distinct()

    def filter_enrolment_health(self, queryset, name, value):
        """
        Filter on the per-programme Enrolment.health_status rather than the
        learner-level overall_health_status rollup.  When a programme filter is
        also active both conditions are applied in a single .filter() span so
        they must match the *same* Enrolment row (avoids cross-join false positives).
        """
        if not value:
            return queryset
        prog_pks = [p for p in self.data.getlist('programme') if p]
        if prog_pks:
            return queryset.filter(
                enrolments__programme_id__in=prog_pks,
                enrolments__health_status__in=value,
            ).distinct()
        return queryset.filter(enrolments__health_status__in=value).distinct()

    def filter_since(self, queryset, name, value):
        try:
            cutoff = date.today() - timedelta(days=int(value))
            return queryset.filter(enrolments__enrolment_date__gte=cutoff).distinct()
        except (ValueError, TypeError):
            return queryset

    def filter_flag(self, queryset, name, value):
        # JSONField __contains is not supported on SQLite; use icontains on the serialised
        # JSON string instead. Wrapping in quotes avoids partial-match false positives
        # (e.g. searching for "inactive" must not hit "never_activated").
        q = Q()
        for flag in value:
            q |= Q(enrolments__active_flags__icontains=f'"{flag}"')
        return queryset.filter(q).distinct()

    def filter_juggler(self, queryset, name, value):
        if value == '1':
            from django.db.models import Count, Q
            return (
                queryset
                .annotate(_subst_count=Count(
                    'enrolments',
                    filter=Q(enrolments__programme__is_prerequisite=False),
                    distinct=True,
                ))
                .filter(_subst_count__gt=1)
            )
        return queryset

    def filter_follow_up(self, queryset, name, value):
        if value == 'due':
            from selfpaced.models import Intervention
            due_learner_pks = (
                Intervention.objects
                .filter(follow_up_required=True, follow_up_date__lte=date.today())
                .values_list('learner_id', flat=True)
                .distinct()
            )
            return queryset.filter(email__in=due_learner_pks)
        return queryset
