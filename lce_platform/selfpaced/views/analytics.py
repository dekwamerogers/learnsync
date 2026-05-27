from collections import defaultdict
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce, Greatest, TruncWeek
from django.shortcuts import render

from selfpaced.models import AssignmentProgress, Course, CourseEnrolment, Enrolment, Learner, Programme
from selfpaced.utils import safe_json

_PROG_PALETTE = [
    '#0452F0','#0d9488','#f97316','#ec4899','#ef4444',
    '#06b6d4','#a855f7','#16a34a','#f59e0b','#dc2626',
]


@login_required
def analytics(request):
    # ── Filters from GET params ───────────────────────────────────────────
    country_filter   = request.GET.getlist('country')
    programme_filter = request.GET.getlist('programme')
    health_filter    = request.GET.getlist('health')
    from_date        = request.GET.get('from_date', '').strip() or None
    to_date          = request.GET.get('to_date', '').strip() or None
    cohort_type      = request.GET.get('cohort_type', 'effective')

    # Build the effective-start expression used for the main date filter.
    # Effective: best available date, taking the LATER of the learner date and
    # the programme start so no-one appears before their programme launched.
    # Fallback chain: enrolment_date → activation_date → programme.start_date
    if cohort_type == 'effective':
        _learner_date = Coalesce('enrolment_date', 'activation_date', 'programme__start_date')
        _prog_date    = Coalesce('programme__start_date', 'enrolment_date', 'activation_date')
        _date_expr    = Greatest(_learner_date, _prog_date)
    elif cohort_type == 'enrolment':
        _date_expr = Coalesce('enrolment_date', 'activation_date')
    else:  # fsol
        _date_expr = F('first_sign_of_life_date')

    # ── Base querysets ────────────────────────────────────────────────────
    # Build a clean (JOIN-free) email subquery first — using a JOIN-based
    # learner_qs directly with .values().annotate() can inflate COUNT results
    # because each learner appears once per matching enrolment row.
    _activity_emails = (
        Learner.objects
        .filter(
            enrolments__has_activity_data=True,
            enrolments__programme__is_prerequisite=False,
        )
        .values_list('email', flat=True)
        .distinct()
    )
    learner_qs = Learner.objects.filter(email__in=_activity_emails)
    if country_filter:
        learner_qs = learner_qs.filter(country__in=country_filter)

    enrolment_qs = (
        Enrolment.objects
        .filter(
            learner__email__in=_activity_emails,
            programme__is_prerequisite=False,
            has_activity_data=True,
        )
        .filter(Q(programme__start_date__isnull=True) | Q(programme__start_date__lte=date.today()))
    )
    if country_filter:
        enrolment_qs = enrolment_qs.filter(learner__country__in=country_filter)

    # Date filter via a separate annotated subquery so enrolment_qs stays
    # annotation-free — annotating the base queryset would add _eff_date to every
    # subsequent GROUP BY and break all the chart aggregations.
    if from_date or to_date:
        _date_sub = Enrolment.objects.annotate(_d=_date_expr)
        if from_date:
            _date_sub = _date_sub.filter(_d__gte=from_date)
        if to_date:
            _date_sub = _date_sub.filter(_d__lte=to_date)
        enrolment_qs = enrolment_qs.filter(pk__in=_date_sub.values('pk'))
        # Narrow learner_qs so health / country charts also respect the date range
        learner_qs = learner_qs.filter(
            email__in=enrolment_qs.values('learner_id')
        )

    if programme_filter:
        enrolment_qs = enrolment_qs.filter(programme_id__in=programme_filter)
        # Also narrow learner_qs so health donut reflects the programme filter
        learner_qs = learner_qs.filter(email__in=enrolment_qs.values('learner_id'))
    if health_filter:
        enrolment_qs = enrolment_qs.filter(health_status__in=health_filter)

    # ── Activated IDs — enrolments where learner passed the first module ─
    # Consistent with the Manager Report definition: CourseEnrolment.is_passed=True
    # for the course with the lowest sequence_number in the enrolment's programme.
    _activated_ids = frozenset(
        CourseEnrolment.objects
        .filter(
            enrolment__in=enrolment_qs,
            is_passed=True,
            course__sequence_number=Subquery(
                Course.objects.filter(
                    programme_id=OuterRef('enrolment__programme_id'),
                ).exclude(code='WALX').order_by('sequence_number').values('sequence_number')[:1]
            ),
        )
        .values_list('enrolment_id', flat=True)
        .distinct()
    )

    # ── Chart 1: Health donut — unique learner overall health ────────────
    health_counts = {
        row['overall_health_status']: row['n']
        for row in learner_qs.values('overall_health_status').annotate(n=Count('email'))
    }
    chart_health_labels = safe_json(['Active', 'At Risk', 'Dormant', 'Graduated', 'Not Started'])
    chart_health_data   = safe_json([
        health_counts.get('active', 0),
        health_counts.get('at_risk', 0),
        health_counts.get('dormant', 0),
        health_counts.get('graduated', 0),
        health_counts.get('not_yet_started', 0),
    ])

    # ── Chart 2: Enrolments by programme (stacked health bar + activated) ─
    _prog_rows = list(
        enrolment_qs
        .values('programme__code')
        .annotate(
            total=Count('pk'),
            activated=Count('pk', filter=Q(pk__in=_activated_ids)),
            active=Count('pk', filter=Q(health_status='active')),
            at_risk=Count('pk', filter=Q(health_status='at_risk')),
            dormant=Count('pk', filter=Q(health_status='dormant')),
            graduated=Count('pk', filter=Q(health_status='graduated')),
            not_yet_started=Count('pk', filter=Q(health_status='not_yet_started')),
        )
        .order_by('programme__code')
    )
    for p in _prog_rows:
        p['activation_rate'] = round(p['activated'] / p['total'] * 100, 1) if p['total'] else 0.0

    chart_prog_labels          = safe_json([r['programme__code']   for r in _prog_rows])
    chart_prog_total           = safe_json([r['total']             for r in _prog_rows])
    chart_prog_activated       = safe_json([r['activated']         for r in _prog_rows])
    chart_prog_activation_rate = safe_json([r['activation_rate']   for r in _prog_rows])
    chart_prog_active          = safe_json([r['active']            for r in _prog_rows])
    chart_prog_at_risk         = safe_json([r['at_risk']           for r in _prog_rows])
    chart_prog_dormant         = safe_json([r['dormant']           for r in _prog_rows])
    chart_prog_graduated       = safe_json([r['graduated']         for r in _prog_rows])
    chart_prog_not_started     = safe_json([r['not_yet_started']   for r in _prog_rows])

    # ── Chart 3: At-risk flag breakdown ──────────────────────────────────
    _FLAG_ORDER = [
        ('inactive',                'Inactive'),
        ('never_activated',         'Never Activated'),
        ('stuck_on_assignment',     'Stuck on Assignment'),
        ('low_pass_rate',           'Low Pass Rate'),
        ('stalled_between_courses', 'Stalled Between Courses'),
        ('stalled_progression',     'No Onward Progress'),
        ('payment_issue',           'Payment Issue'),
    ]
    _flag_learners: dict = defaultdict(set)
    for row in (
        enrolment_qs
        .filter(health_status__in=['at_risk', 'dormant'])
        .values('learner_id', 'active_flags')
    ):
        for flag in (row['active_flags'] or []):
            _flag_learners[flag].add(row['learner_id'])
    chart_flag_labels = safe_json([label for _, label in _FLAG_ORDER])
    chart_flag_counts = safe_json([len(_flag_learners.get(code, set())) for code, _ in _FLAG_ORDER])

    # ── Chart 4: Learners by country ─────────────────────────────────────
    _country_rows = (
        learner_qs
        .exclude(country='')
        .values('country')
        .annotate(n=Count('email'))
        .order_by('-n')[:20]
    )
    chart_country_labels = safe_json([r['country'] for r in _country_rows])
    chart_country_counts = safe_json([r['n']       for r in _country_rows])

    # ── Chart 5: Portfolio mix — single vs multi-programme ───────────────
    _learner_prog_counts = (
        enrolment_qs
        .values('learner_id')
        .annotate(n=Count('programme_id', distinct=True))
    )
    solo  = sum(1 for r in _learner_prog_counts if r['n'] == 1)
    multi = sum(1 for r in _learner_prog_counts if r['n'] >  1)

    # ── Chart 6: Course distribution (interactive, per programme) ────────
    active_progs = list(Programme.objects.filter(
        is_active=True, is_prerequisite=False,
    ).filter(
        Q(start_date__isnull=True) | Q(start_date__lte=date.today())
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=date.today())
    ).order_by('code'))
    if programme_filter:
        active_progs = [p for p in active_progs if str(p.pk) in programme_filter]

    prog_pks = [p.pk for p in active_progs]

    _course_dist: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for row in (
        enrolment_qs
        .filter(current_course__isnull=False)
        .values('programme_id', 'current_course__sequence_number', 'current_course__full_name', 'health_status')
        .annotate(n=Count('learner_id', distinct=True))
        .order_by('programme_id', 'current_course__sequence_number')
    ):
        prog_id   = row['programme_id']
        seq       = row['current_course__sequence_number'] or 0
        full_name = row['current_course__full_name'] or f'Course {seq}'
        _course_dist[prog_id][(seq, full_name)][row['health_status']] = row['n']

    _badges_by_course: dict = defaultdict(dict)
    for row in (
        CourseEnrolment.objects
        .filter(status='completed', course__programme_id__in=prog_pks,
                enrolment__in=enrolment_qs)
        .values('course__programme_id', 'course__sequence_number', 'course__full_name')
        .annotate(n=Count('id'))
    ):
        prog_id   = row['course__programme_id']
        seq       = row['course__sequence_number'] or 0
        full_name = row['course__full_name'] or f'Course {seq}'
        _badges_by_course[prog_id][(seq, full_name)] = row['n']

    _all_course_keys: dict = defaultdict(set)
    for prog_id, courses in _course_dist.items():
        _all_course_keys[prog_id].update(courses.keys())
    for prog_id, courses in _badges_by_course.items():
        _all_course_keys[prog_id].update(courses.keys())

    course_chart_data = []
    for prog in active_progs:
        keys = _all_course_keys.get(prog.pk, set())
        courses = []
        for (seq, full_name) in sorted(keys):
            by_health = dict(_course_dist[prog.pk].get((seq, full_name), {}))
            courses.append({
                'seq':      seq,
                'label':    full_name,
                'by_health': by_health,
                'badges':   _badges_by_course[prog.pk].get((seq, full_name), 0),
                'total':    sum(by_health.values()),
            })
        course_chart_data.append({
            'pk':      prog.pk,
            'code':    prog.code,
            'name':    prog.name,
            'courses': courses,
        })

    # ── Cohort breakdown — fresh queryset with the date annotation ───────
    _cohort_raw = list(
        Enrolment.objects
        .filter(pk__in=enrolment_qs.values('pk'))
        .annotate(_eff=_date_expr)
        .exclude(_eff__isnull=True)
        .annotate(week=TruncWeek('_eff'))
        .values('week', 'health_status')
        .annotate(n=Count('id'))
        .order_by('week')
    )

    _cohort_weeks: dict = defaultdict(lambda: defaultdict(int))
    for row in _cohort_raw:
        w = row['week']
        _cohort_weeks[w.date() if hasattr(w, 'hour') else w][row['health_status']] = row['n']

    # Per-week activated count (passed first module) — bucketed by same date expr
    _activated_cohort_raw = list(
        Enrolment.objects
        .filter(pk__in=list(_activated_ids))
        .annotate(_eff=_date_expr)
        .exclude(_eff__isnull=True)
        .annotate(week=TruncWeek('_eff'))
        .values('week')
        .annotate(n=Count('id'))
        .order_by('week')
    )
    _activated_by_week: dict = {}
    for row in _activated_cohort_raw:
        w = row['week']
        _activated_by_week[w.date() if hasattr(w, 'hour') else w] = row['n']

    cohort_data = []
    for week_start in sorted(_cohort_weeks.keys()):
        h           = _cohort_weeks[week_start]
        week_end    = week_start + timedelta(days=6)
        total       = sum(h.values())
        activated   = _activated_by_week.get(week_start, 0)
        active      = h.get('active', 0)
        at_risk     = h.get('at_risk', 0)
        dormant     = h.get('dormant', 0)
        graduated   = h.get('graduated', 0)
        not_started = h.get('not_yet_started', 0)
        cohort_data.append({
            'label':           f'{week_start.strftime("%d %b")} – {week_end.strftime("%d %b")}',
            'week_start':      week_start.isoformat(),
            'week_end':        week_end.isoformat(),
            'total':           total,
            'activated':       activated,
            'active':          active,
            'at_risk':         at_risk,
            'dormant':         dormant,
            'graduated':       graduated,
            'not_started':     not_started,
            'activation_rate': round(activated / total * 100, 1) if total else 0,
            'active_rate':     round(active    / total * 100, 1) if total else 0,
            'at_risk_rate':    round(at_risk   / total * 100, 1) if total else 0,
            'dormant_rate':    round(dormant   / total * 100, 1) if total else 0,
            'graduated_rate':  round(graduated / total * 100, 1) if total else 0,
        })

    # ── Progression over time ─────────────────────────────────────────────
    # Uses actual dates (activation_date, completion_date) — not upload dates.
    # Both series are filtered to the same enrolment_qs scope (programme, country,
    # date range) so the filter bar controls all charts consistently.

    _prog_code_map = {p.pk: p.code for p in active_progs}

    def _as_date(v):
        return v.date() if hasattr(v, 'hour') else v

    # Series 1: daily unique active learners per programme
    # "Active" = accessed or submitted at least one assignment on that day.
    # Two queries merged in Python so each (date, programme, learner) triple
    # is deduplicated before counting.
    _raw_activity: set = set()
    for r in (
        AssignmentProgress.objects
        .filter(course_enrolment__enrolment__in=enrolment_qs, accessed_date__isnull=False)
        .values('accessed_date', 'course_enrolment__enrolment__programme_id',
                'course_enrolment__enrolment__learner_id')
    ):
        _raw_activity.add((_as_date(r['accessed_date']),
                            r['course_enrolment__enrolment__programme_id'],
                            r['course_enrolment__enrolment__learner_id']))
    for r in (
        AssignmentProgress.objects
        .filter(course_enrolment__enrolment__in=enrolment_qs, submitted_date__isnull=False)
        .values('submitted_date', 'course_enrolment__enrolment__programme_id',
                'course_enrolment__enrolment__learner_id')
    ):
        _raw_activity.add((_as_date(r['submitted_date']),
                            r['course_enrolment__enrolment__programme_id'],
                            r['course_enrolment__enrolment__learner_id']))

    # ── Day-of-week activity (Mon=0 … Sun=6) ─────────────────────────────
    # Count unique learners who were active on each day of the week
    _dow_learners: dict = defaultdict(set)
    for _d, _prog_id, _learner_id in _raw_activity:
        _dow_learners[_d.weekday()].add(_learner_id)
    dow_activity = safe_json([len(_dow_learners.get(i, set())) for i in range(7)])

    # Roll up to (date, programme_id) → unique learner count
    _act_by_prog_date: dict = defaultdict(lambda: defaultdict(int))
    for d, prog_id, _ in _raw_activity:
        _act_by_prog_date[prog_id][d] += 1

    # Series 2: daily course completions per programme
    _comp_rows = list(
        CourseEnrolment.objects
        .filter(
            enrolment__in=enrolment_qs,
            status='completed',
            completion_date__isnull=False,
        )
        .values('completion_date', 'enrolment__programme_id')
        .annotate(n=Count('id'))
        .order_by('completion_date')
    )

    # Build the X axis starting from the earliest programme start_date so the
    # chart begins at a meaningful origin rather than the first data point.
    _prog_starts = [p.start_date for p in active_progs if p.start_date]
    _range_start = min(_prog_starts) if _prog_starts else None

    _data_dates = (
        {d for prog in _act_by_prog_date.values() for d in prog}
        | {_as_date(r['completion_date']) for r in _comp_rows}
    )
    _range_end = max(_data_dates) if _data_dates else date.today()

    if _range_start and _range_start <= _range_end:
        _all_days = [
            _range_start + timedelta(days=i)
            for i in range((_range_end - _range_start).days + 1)
        ]
    else:
        _all_days = sorted(_data_dates)

    prog_week_labels = safe_json([d.strftime('%d %b') for d in _all_days])

    def _series_from_dict(by_prog_date, cumulative=False):
        """Build Chart.js datasets from a {prog_id: {date: count}} dict."""
        datasets = []
        for i, prog_id in enumerate(sorted(by_prog_date.keys())):
            code = _prog_code_map.get(prog_id, str(prog_id))
            if cumulative:
                running, data = 0, []
                for d in _all_days:
                    running += by_prog_date[prog_id].get(d, 0)
                    data.append(running)
            else:
                data = [by_prog_date[prog_id].get(d, 0) for d in _all_days]
            datasets.append({
                'label': code,
                'data': data,
                'borderColor': _PROG_PALETTE[i % len(_PROG_PALETTE)],
                'backgroundColor': 'transparent',
                'tension': 0.3,
                'pointRadius': 2,
                'borderWidth': 2,
                'fill': False,
            })
        return datasets

    def _series_from_rows(rows, prog_id_key, date_key, cumulative=False):
        """Build Chart.js datasets from a list of annotated queryset rows."""
        by_prog: dict = defaultdict(lambda: defaultdict(int))
        for r in rows:
            by_prog[r[prog_id_key]][_as_date(r[date_key])] += r['n']
        return _series_from_dict(by_prog, cumulative=cumulative)

    activation_datasets            = safe_json(_series_from_dict(_act_by_prog_date))
    activation_cumulative_datasets = safe_json(_series_from_dict(_act_by_prog_date, cumulative=True))
    completion_datasets            = safe_json(_series_from_rows(_comp_rows, 'enrolment__programme_id', 'completion_date'))
    completion_cumulative_datasets = safe_json(_series_from_rows(_comp_rows, 'enrolment__programme_id', 'completion_date', cumulative=True))

    # Series 3: daily first-module completions per programme
    # "Activated" = passed the first course (lowest sequence_number) of their programme.
    _first_module_rows = list(
        CourseEnrolment.objects
        .filter(
            enrolment__in=enrolment_qs,
            is_passed=True,
            completion_date__isnull=False,
            course__sequence_number=Subquery(
                Course.objects.filter(
                    programme_id=OuterRef('enrolment__programme_id'),
                ).exclude(code='WALX').order_by('sequence_number').values('sequence_number')[:1]
            ),
        )
        .values('completion_date', 'enrolment__programme_id')
        .annotate(n=Count('id'))
        .order_by('completion_date')
    )
    first_module_datasets            = safe_json(_series_from_rows(_first_module_rows, 'enrolment__programme_id', 'completion_date'))
    first_module_cumulative_datasets = safe_json(_series_from_rows(_first_module_rows, 'enrolment__programme_id', 'completion_date', cumulative=True))

    progression_has_data = bool(_all_days)

    # ── Filter option lists ───────────────────────────────────────────────
    all_countries = (
        Learner.objects.exclude(country='')
        .values_list('country', flat=True)
        .distinct().order_by('country')
    )
    all_programmes = Programme.objects.filter(
        is_active=True, is_prerequisite=False,
    ).filter(
        Q(start_date__isnull=True) | Q(start_date__lte=date.today())
    ).order_by('code')
    health_choices = [
        ('active', 'Active'), ('at_risk', 'At Risk'), ('dormant', 'Dormant'),
        ('graduated', 'Graduated'), ('not_yet_started', 'Not Started'),
    ]

    return render(request, 'selfpaced/analytics.html', {
        # filter state
        'country_filter':   country_filter,
        'programme_filter': programme_filter,
        'health_filter':    health_filter,
        'from_date':        from_date or '',
        'to_date':          to_date or '',
        # filter option lists
        'all_countries':    all_countries,
        'all_programmes':   all_programmes,
        'health_choices':   health_choices,
        # chart data
        'chart_health_labels':    chart_health_labels,
        'chart_health_data':      chart_health_data,
        'chart_prog_labels':           chart_prog_labels,
        'chart_prog_total':            chart_prog_total,
        'chart_prog_activated':        chart_prog_activated,
        'chart_prog_activation_rate':  chart_prog_activation_rate,
        'chart_prog_active':           chart_prog_active,
        'chart_prog_at_risk':          chart_prog_at_risk,
        'chart_prog_dormant':          chart_prog_dormant,
        'chart_prog_graduated':        chart_prog_graduated,
        'chart_prog_not_started':      chart_prog_not_started,
        'chart_flag_labels':      chart_flag_labels,
        'chart_flag_counts':      chart_flag_counts,
        'chart_country_labels':   chart_country_labels,
        'chart_country_counts':   chart_country_counts,
        'portfolio_solo':         solo,
        'portfolio_multi':        multi,
        'dow_activity':           dow_activity,
        'course_chart_data':      safe_json(course_chart_data),
        'active_progs':           active_progs,
        # cohort
        'cohort_type':  cohort_type,
        'cohort_data':  safe_json(cohort_data),
        'cohort_rows':  cohort_data,
        # progression over time
        'prog_week_labels':              prog_week_labels,
        'activation_datasets':                activation_datasets,
        'activation_cumulative_datasets':     activation_cumulative_datasets,
        'completion_datasets':                completion_datasets,
        'completion_cumulative_datasets':     completion_cumulative_datasets,
        'first_module_datasets':              first_module_datasets,
        'first_module_cumulative_datasets':   first_module_cumulative_datasets,
        'progression_has_data':               progression_has_data,
    })
