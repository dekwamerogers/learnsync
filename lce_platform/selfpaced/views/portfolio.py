from collections import defaultdict
from datetime import date as _date

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render

from selfpaced.models import Course, CourseEnrolment, Enrolment, Programme
from selfpaced.querysets import real_learners_qs


@login_required
def portfolio(request):
    today = _date.today()
    programmes = list(
        Programme.objects.filter(is_active=True, is_prerequisite=False)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .order_by('code')
    )
    prog_pks = [p.pk for p in programmes]

    # Base queryset — paid learners only (matches programme detail page)
    paid_enrolments = (
        Enrolment.objects
        .filter(programme_id__in=prog_pks)
        .exclude(learner__payment_status='unknown')
    )

    # Health breakdown per programme
    health_by_prog: dict = defaultdict(lambda: defaultdict(int))
    for row in (
        paid_enrolments
        .values('programme_id', 'health_status')
        .annotate(n=Count('id'))
    ):
        health_by_prog[row['programme_id']][row['health_status']] = row['n']

    # Course counts
    course_count_by_prog = {
        row['programme_id']: row['n']
        for row in Course.objects
        .filter(is_active=True, programme_id__in=prog_pks)
        .values('programme_id').annotate(n=Count('id'))
    }

    # Unique paid learners per programme
    learner_count_by_prog = {
        row['programme_id']: row['n']
        for row in paid_enrolments
        .values('programme_id')
        .annotate(n=Count('learner_id', distinct=True))
    }

    # Badge + certificate counts
    badge_count_by_prog = {
        row['course__programme_id']: row['n']
        for row in CourseEnrolment.objects
        .filter(status='completed', course__programme_id__in=prog_pks)
        .values('course__programme_id').annotate(n=Count('id'))
    }

    rows = []
    for prog in programmes:
        h = health_by_prog.get(prog.pk, {})
        total = sum(h.values())
        dormant = h.get('dormant', 0)
        at_risk = h.get('at_risk', 0)
        active = h.get('active', 0)
        graduated = h.get('graduated', 0)
        not_started = h.get('not_yet_started', 0)
        learners = learner_count_by_prog.get(prog.pk, 0)
        activated = learners - not_started
        activation_rate = round(activated / learners * 100) if learners else None
        grad_rate = round(graduated / activated * 100) if activated else None
        concern = dormant + at_risk
        rows.append({
            'programme': prog,
            'total': total,
            'learners': learners,
            'dormant': dormant,
            'at_risk': at_risk,
            'active': active,
            'graduated': graduated,
            'not_started': not_started,
            'activation_rate': activation_rate,
            'grad_rate': grad_rate,
            'concern': concern,
            'courses': course_count_by_prog.get(prog.pk, 0),
            'badges': badge_count_by_prog.get(prog.pk, 0),
        })

    # Sort: most concerning first (dormant + at_risk desc)
    rows.sort(key=lambda r: -r['concern'])

    # Portfolio-level totals
    total_learners = real_learners_qs().count()
    total_enrolments = paid_enrolments.count()
    total_dormant = sum(r['dormant'] for r in rows)
    total_at_risk = sum(r['at_risk'] for r in rows)
    total_active = sum(r['active'] for r in rows)
    total_graduated = sum(r['graduated'] for r in rows)
    total_not_started = sum(r['not_started'] for r in rows)

    return render(request, 'selfpaced/portfolio.html', {
        'rows': rows,
        'total_learners': total_learners,
        'total_enrolments': total_enrolments,
        'total_dormant': total_dormant,
        'total_at_risk': total_at_risk,
        'total_active': total_active,
        'total_graduated': total_graduated,
        'total_not_started': total_not_started,
    })
