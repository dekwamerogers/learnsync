from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F, Max, Prefetch
from django.db.models.functions import Coalesce, Greatest
from django.shortcuts import get_object_or_404, render

from selfpaced.exports import export_learners_csv
from selfpaced.filters import LearnerFilter
from selfpaced.models import (
    AssignmentProgress,
    CourseEnrolment,
    Enrolment,
    EnrolmentSnapshot,
    Intervention,
    Learner,
    Pod,
    PodAssignment,
)
from selfpaced.querysets import real_learners_qs


def _page_display_range(page_obj):
    """Return page numbers (int) and None for ellipsis gaps."""
    paginator = page_obj.paginator
    current = page_obj.number
    total = paginator.num_pages
    shown = set([1, total] + list(range(max(1, current - 2), min(total + 1, current + 3))))
    result = []
    prev = None
    for p in sorted(shown):
        if prev and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result


@login_required
def learner_list(request):
    qs = Learner.objects.prefetch_related(
        Prefetch(
            'enrolments',
            queryset=Enrolment.objects
            .select_related('programme', 'current_course')
            .annotate(last_activity=Max('course_enrolments__last_activity_date'))
            .order_by(F('last_activity').desc(nulls_last=True)),
        )
    )
    # Exclude unpaid learners by default unless the user has explicitly filtered by payment status
    if not request.GET.getlist('payment'):
        qs = qs.exclude(payment_status='unknown')
    f = LearnerFilter(request.GET, queryset=qs)
    distinct_qs = f.qs.distinct()

    # Enrollment date range filter (used by cohort chart drill-down)
    enrol_from   = request.GET.get('enrol_from', '').strip() or None
    enrol_to     = request.GET.get('enrol_to',   '').strip() or None
    cohort_basis = request.GET.get('cohort_basis', 'effective')

    if enrol_from or enrol_to:
        if cohort_basis == 'effective':
            _ldate = Coalesce('enrolment_date', 'activation_date', 'programme__start_date')
            _pdate = Coalesce('programme__start_date', 'enrolment_date', 'activation_date')
            _expr  = Greatest(_ldate, _pdate)
        elif cohort_basis == 'enrolment':
            _expr = Coalesce('enrolment_date', 'activation_date')
        else:  # fsol
            _expr = F('first_sign_of_life_date')

        enrol_sub = (
            Enrolment.objects
            .annotate(_d=_expr)
            .exclude(_d__isnull=True)
        )
        if enrol_from:
            enrol_sub = enrol_sub.filter(_d__gte=enrol_from)
        if enrol_to:
            enrol_sub = enrol_sub.filter(_d__lte=enrol_to)
        distinct_qs = distinct_qs.filter(enrolments__in=enrol_sub.values('pk')).distinct()

    if request.GET.get('export') == 'csv':
        return export_learners_csv(distinct_qs)

    paginator = Paginator(distinct_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'selfpaced/learner_list.html', {
        'filter':        f,
        'page_obj':      page_obj,
        'total':         paginator.count,
        'total_all':     real_learners_qs().exclude(payment_status='unknown').count(),
        'page_range':    _page_display_range(page_obj),
        'enrol_from':    enrol_from or '',
        'enrol_to':      enrol_to   or '',
        'cohort_basis':  cohort_basis,
    })


@login_required
def learner_profile(request, email):
    learner = get_object_or_404(Learner, pk=email)
    enrolments = list(
        Enrolment.objects
        .filter(learner=learner)
        .select_related('programme', 'current_course')
        .annotate(last_activity=Max('course_enrolments__last_activity_date'))
        .order_by(F('last_activity').desc(nulls_last=True))
        .prefetch_related(
            Prefetch(
                'course_enrolments',
                queryset=CourseEnrolment.objects
                .select_related('course')
                .prefetch_related(
                    Prefetch(
                        'assignment_progress',
                        queryset=AssignmentProgress.objects
                        .select_related('assignment')
                        .order_by('assignment__sequence_in_course'),
                    )
                )
                .order_by('course__sequence_number'),
            ),
            Prefetch(
                'snapshots',
                queryset=EnrolmentSnapshot.objects
                .filter(is_deleted=False)
                .order_by('-snapshot_date'),
                to_attr='recent_snapshots',
            ),
        )
    )
    interventions = (
        Intervention.objects
        .filter(learner=learner)
        .select_related('logged_by', 'enrolment__programme')
        .order_by('-intervention_date')[:10]
    )

    # Pod assignments — current assignment per programme
    current_assignments = {
        pa.programme_id: pa
        for pa in PodAssignment.objects
        .filter(learner=learner, is_current=True)
        .select_related('pod', 'programme')
    }

    # Available pods per programme the learner is enrolled in (for assign/switch dropdowns)
    enrolled_prog_ids = [e.programme_id for e in enrolments if not e.programme.is_prerequisite]
    pods_by_programme = {}
    for pod in (
        Pod.objects
        .filter(programme_id__in=enrolled_prog_ids, status='active')
        .select_related('programme')
        .order_by('programme__code', 'target_month')
    ):
        pods_by_programme.setdefault(pod.programme_id, []).append(pod)

    # Build per-enrolment pod context
    pod_rows = []
    for enrolment in enrolments:
        if enrolment.programme.is_prerequisite:
            continue
        pod_rows.append({
            'enrolment':    enrolment,
            'current':      current_assignments.get(enrolment.programme_id),
            'available_pods': pods_by_programme.get(enrolment.programme_id, []),
        })

    return render(request, 'selfpaced/learner_profile.html', {
        'learner': learner,
        'enrolments': enrolments,
        'interventions': interventions,
        'pod_rows': pod_rows,
    })
