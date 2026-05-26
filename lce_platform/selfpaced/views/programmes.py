from collections import defaultdict
from datetime import date as _date, timedelta as _timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Min, Q
from django.db.models.functions import Coalesce, Greatest, TruncMonth
from django.shortcuts import get_object_or_404, render

from selfpaced.models import Course, CourseEnrolment, Enrolment, EnrolmentSnapshot, HealthStatus, IngestionJob, Learner, Programme


def _active_programmes_qs():
    """Programmes visible in learner-facing views: active flag + not past end_date."""
    today = _date.today()
    return Programme.objects.filter(
        is_active=True,
        is_prerequisite=False,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    )


@login_required
def programme_list(request):
    """Fast initial render — just programme names. Stats loaded async via HTMX."""
    programmes = list(_active_programmes_qs().order_by('code'))
    return render(request, 'selfpaced/programme_list.html', {'programmes': programmes})


@login_required
def programme_list_stats(request):
    """HTMX endpoint — returns populated tbody rows with health/course/badge stats."""
    programmes = list(_active_programmes_qs().order_by('code'))
    prog_pks = [p.pk for p in programmes]

    # 1. Health breakdown — activity learners only (has_activity_data=True), paid only
    health_by_prog: dict = defaultdict(lambda: defaultdict(int))
    for row in (
        Enrolment.objects
        .filter(programme_id__in=prog_pks, has_activity_data=True)
        .exclude(learner__payment_status='unknown')
        .values('programme_id', 'health_status')
        .annotate(n=Count('id'))
    ):
        health_by_prog[row['programme_id']][row['health_status']] = row['n']

    # 2. Active course count — one GROUP BY query
    course_count_by_prog = {
        row['programme_id']: row['n']
        for row in Course.objects
        .filter(is_active=True, programme_id__in=prog_pks)
        .values('programme_id')
        .annotate(n=Count('id'))
    }

    # 3. Badge count — total course completions per programme (1 badge per course per learner)
    badge_count_by_prog = {
        row['course__programme_id']: row['n']
        for row in CourseEnrolment.objects
        .filter(status='completed', course__programme_id__in=prog_pks)
        .values('course__programme_id')
        .annotate(n=Count('id'))
    }

    # 4. Activation & retention — completed first course / enrolled in course 2+
    _min_seq = dict(
        Course.objects
        .filter(is_active=True, programme_id__in=prog_pks)
        .exclude(code='WALX')   # WALX completions live on the standalone WALX enrolment, not the main programme enrolment
        .values('programme_id')
        .annotate(ms=Min('sequence_number'))
        .values_list('programme_id', 'ms')
    )
    activated_by_prog: dict = {}
    retained_by_prog: dict  = {}
    if _min_seq:
        act_q = ret_q = None
        for pid, ms in _min_seq.items():
            # Activated: passed Module 1 (is_passed=True on the lowest-sequence course)
            ca = Q(enrolment__programme_id=pid, course__sequence_number=ms, is_passed=True)
            # Retained: passed Module 1 AND enrolment is still active/at_risk/graduated
            cr = Q(
                enrolment__programme_id=pid,
                course__sequence_number=ms,
                is_passed=True,
                enrolment__health_status__in=['active', 'at_risk', 'graduated'],
            )
            act_q = ca if act_q is None else act_q | ca
            ret_q = cr if ret_q is None else ret_q | cr
        for row in (CourseEnrolment.objects.filter(act_q)
                    .filter(enrolment__has_activity_data=True)
                    .exclude(enrolment__learner__payment_status='unknown')
                    .values('enrolment__programme_id')
                    .annotate(n=Count('enrolment_id', distinct=True))):
            activated_by_prog[row['enrolment__programme_id']] = row['n']
        for row in (CourseEnrolment.objects.filter(ret_q)
                    .filter(enrolment__has_activity_data=True)
                    .exclude(enrolment__learner__payment_status='unknown')
                    .values('enrolment__programme_id')
                    .annotate(n=Count('enrolment_id', distinct=True))):
            retained_by_prog[row['enrolment__programme_id']] = row['n']

    # Attach computed stats to programme objects
    for prog in programmes:
        h = health_by_prog.get(prog.pk, {})
        prog.total_enrolments = sum(h.values())
        prog.active_count = h.get('active', 0)
        prog.at_risk_count = h.get('at_risk', 0)
        prog.dormant_count = h.get('dormant', 0)
        prog.graduated_count = h.get('graduated', 0)
        prog.not_yet_started_count = h.get('not_yet_started', 0)
        prog.certificates_count = h.get('graduated', 0)
        prog.course_count = course_count_by_prog.get(prog.pk, 0)
        prog.badges_count = badge_count_by_prog.get(prog.pk, 0)
        prog.activated_count = activated_by_prog.get(prog.pk, 0)
        prog.retained_count  = retained_by_prog.get(prog.pk, 0)
        prog.activation_rate = round(prog.activated_count / prog.total_enrolments * 100) if prog.total_enrolments else 0
        prog.retention_rate  = round(prog.retained_count  / prog.activated_count  * 100) if prog.activated_count  else 0

    # Solo vs multi-programme learner mix — activity learners only, paid only.
    # Prerequisite programmes (e.g. WALX) are excluded — they are onboarding
    # pathways, not substantive programmes, so a learner in WALX + COCR counts
    # as a solo learner, not a multi-programme learner.
    # Query is NOT scoped to prog_pks so that enrolments in inactive programmes
    # are still counted when deciding if a learner is "multi".
    _learner_progs: dict = defaultdict(set)
    for learner_id, prog_pk in (
        Enrolment.objects
        .filter(has_activity_data=True)
        .exclude(programme__is_prerequisite=True)
        .exclude(learner__payment_status='unknown')
        .values_list('learner_id', 'programme_id')
    ):
        _learner_progs[learner_id].add(prog_pk)

    prog_mix: dict = defaultdict(lambda: {'solo': 0, 'multi': 0})
    for learner_id, pks in _learner_progs.items():
        kind = 'multi' if len(pks) > 1 else 'solo'
        for pk in pks:
            prog_mix[pk][kind] += 1

    # Deltas vs previous completed upload
    prog_deltas: dict = {}
    jobs = list(IngestionJob.objects.filter(status='complete').order_by('-uploaded_at')[:2])
    if len(jobs) >= 2:
        prev_by_prog: dict = defaultdict(lambda: defaultdict(int))
        for row in (
            EnrolmentSnapshot.objects
            .filter(ingestion_job=jobs[1])
            .exclude(payment_status='unknown')
            .values('programme_id', 'health_status')
            .annotate(n=Count('id'))
        ):
            prev_by_prog[row['programme_id']][row['health_status']] = row['n']

        for prog in programmes:
            prev = prev_by_prog.get(prog.pk, {})
            prog_deltas[prog.pk] = {
                'total':     prog.total_enrolments - sum(prev.values()),
                'active':    prog.active_count    - prev.get('active',    0),
                'at_risk':   prog.at_risk_count   - prev.get('at_risk',   0),
                'dormant':   prog.dormant_count   - prev.get('dormant',   0),
                'graduated': prog.graduated_count - prev.get('graduated', 0),
            }

    # Unique paid-learner count across all active programmes (activity learners only)
    unique_learner_total = (
        Enrolment.objects
        .filter(programme_id__in=prog_pks, has_activity_data=True)
        .exclude(learner__payment_status='unknown')
        .values('learner_id')
        .distinct()
        .count()
    )

    # Onboarded = graduated from a prerequisite programme (e.g. WALX)
    onboarded_ids = set(
        Enrolment.objects.filter(
            programme__is_prerequisite=True,
            is_graduated=True,
        ).values_list('learner_id', flat=True)
    )
    _prog_learner_ids: dict = defaultdict(set)
    for learner_id, prog_id in (
        Enrolment.objects
        .filter(programme_id__in=prog_pks, has_activity_data=True)
        .values_list('learner_id', 'programme_id')
    ):
        _prog_learner_ids[prog_id].add(learner_id)
    for prog in programmes:
        prog.onboarded_count = len(_prog_learner_ids.get(prog.pk, set()) & onboarded_ids)

    return render(request, 'selfpaced/_programme_rows.html', {
        'programmes': programmes,
        'prog_deltas': prog_deltas,
        'prog_mix': dict(prog_mix),
        'unique_learner_total': unique_learner_total,
    })


@login_required
def programme_detail(request, pk):
    programme = get_object_or_404(Programme, pk=pk)
    tab = request.GET.get('tab', 'overview')

    # All paid enrolments — used for the learner list (staff can see enrollment-only rows).
    _paid_enrolments = Enrolment.objects.filter(programme=programme).exclude(learner__payment_status='unknown')
    # Activity-only enrolments — used for health counts / metrics.
    _activity_enrolments = _paid_enrolments.filter(has_activity_data=True)

    _raw_counts = (
        _activity_enrolments
        .values('health_status')
        .annotate(n=Count('id'))
    )
    health_counts = {s: 0 for s in ('dormant', 'at_risk', 'active', 'graduated', 'not_yet_started')}
    for row in _raw_counts:
        if row['health_status'] in health_counts:
            health_counts[row['health_status']] = row['n']
    total = sum(health_counts.values())

    courses = list(programme.courses.order_by('sequence_number'))

    # Per-course enrolment stats — paid learners only
    ce_stats = (
        CourseEnrolment.objects
        .filter(course__in=courses, enrolment__programme=programme)
        .exclude(enrolment__learner__payment_status='unknown')
        .values('course_id', 'status')
        .annotate(count=Count('enrolment__learner_id', distinct=True))
    )
    stats_by_course = defaultdict(lambda: defaultdict(int))
    for row in ce_stats:
        stats_by_course[row['course_id']][row['status']] = row['count']
    for course in courses:
        s = stats_by_course[course.pk]
        course.enrolled_count = sum(s.values())
        course.in_progress_count = s.get('in_progress', 0)
        course.completed_count = s.get('completed', 0)

    enrolments_qs = (
        _paid_enrolments
        .select_related('learner', 'current_course')
        .order_by('health_status', 'learner__last_name', 'learner__first_name')
    )
    paginator = Paginator(enrolments_qs, 50)
    enrolments = paginator.get_page(request.GET.get('page'))

    health_display = [
        {'label': 'Dormant',       'color': '#7c3aed', 'count': health_counts['dormant']},
        {'label': 'At Risk',       'color': '#d97706', 'count': health_counts['at_risk']},
        {'label': 'Active',        'color': '#16a34a', 'count': health_counts['active']},
        {'label': 'Graduated',     'color': '#2563eb', 'count': health_counts['graduated']},
        {'label': 'Not Started',   'color': '#9ca3af', 'count': health_counts['not_yet_started']},
    ]

    # Flag breakdown — activity enrolments only
    _flag_counts: dict = defaultdict(int)
    for row in (
        _activity_enrolments
        .filter(health_status__in=['at_risk', 'dormant'])
        .values('active_flags')
    ):
        for flag in (row['active_flags'] or []):
            _flag_counts[flag] += 1

    _FLAG_DISPLAY = [
        ('inactive',                'Inactive',              '#b45309'),
        ('never_activated',         'Never Activated',       '#b45309'),
        ('stuck_on_assignment',     'Stuck on Assignment',   '#b45309'),
        ('low_pass_rate',           'Low Pass Rate',         '#dc2626'),
        ('stalled_between_courses', 'Stalled Between Courses', '#b45309'),
        ('stalled_progression',     'No Onward Progress',   '#7c3aed'),
        ('payment_issue',           'Payment Issue',         '#dc2626'),
    ]
    flag_breakdown = [
        {'code': code, 'label': label, 'color': color, 'count': _flag_counts.get(code, 0)}
        for code, label, color in _FLAG_DISPLAY
        if _flag_counts.get(code, 0) > 0
    ]

    return render(request, 'selfpaced/programme_detail.html', {
        'programme': programme,
        'tab': tab,
        'health_counts': health_counts,
        'health_display': health_display,
        'total': total,
        'courses': courses,
        'enrolments': enrolments,
        'flag_breakdown': flag_breakdown,
    })


@login_required
def programme_charts(request):
    """HTMX endpoint — chart data for the programme list page."""
    programmes = list(_active_programmes_qs().order_by('code'))
    prog_pks = [p.pk for p in programmes]

    # Health breakdown per programme — activity enrolments only, paid only
    health_by_prog: dict = defaultdict(lambda: defaultdict(int))
    for row in (
        Enrolment.objects
        .filter(programme_id__in=prog_pks, has_activity_data=True)
        .exclude(learner__payment_status='unknown')
        .values('programme_id', 'health_status')
        .annotate(n=Count('id'))
    ):
        health_by_prog[row['programme_id']][row['health_status']] = row['n']

    # Solo vs multi-programme learner mix — activity learners only, paid only
    _learner_progs: dict = defaultdict(set)
    for learner_id, prog_pk in (
        Enrolment.objects
        .filter(has_activity_data=True)
        .exclude(programme__is_prerequisite=True)
        .exclude(learner__payment_status='unknown')
        .values_list('learner_id', 'programme_id')
    ):
        _learner_progs[learner_id].add(prog_pk)

    global_solo = sum(1 for pks in _learner_progs.values() if len(pks) == 1)
    global_multi = sum(1 for pks in _learner_progs.values() if len(pks) > 1)

    prog_mix: dict = defaultdict(lambda: {'solo': 0, 'multi': 0})
    for learner_id, pks in _learner_progs.items():
        kind = 'multi' if len(pks) > 1 else 'solo'
        for pk in pks:
            prog_mix[pk][kind] += 1

    # Learner distribution by current course and health status — activity enrolments only, paid only
    _course_health: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for row in (
        Enrolment.objects
        .filter(programme_id__in=prog_pks, has_activity_data=True, current_course__isnull=False)
        .exclude(learner__payment_status='unknown')
        .values('programme_id', 'current_course__sequence_number', 'current_course__full_name', 'health_status')
        .annotate(n=Count('learner_id', distinct=True))
        .order_by('programme_id', 'current_course__sequence_number')
    ):
        prog_id = row['programme_id']
        seq = row['current_course__sequence_number'] or 0
        full_name = row['current_course__full_name'] or f'Course {seq}'
        _course_health[prog_id][(seq, full_name)][row['health_status']] = row['n']

    # Badges acquired per course — activity enrolments only, paid only
    _badges_by_course: dict = defaultdict(dict)
    for row in (
        CourseEnrolment.objects
        .filter(status='completed', course__programme_id__in=prog_pks, enrolment__has_activity_data=True)
        .exclude(enrolment__learner__payment_status='unknown')
        .values('course__programme_id', 'course__sequence_number', 'course__full_name')
        .annotate(n=Count('id'))
    ):
        prog_id = row['course__programme_id']
        seq = row['course__sequence_number'] or 0
        full_name = row['course__full_name'] or f'Course {seq}'
        _badges_by_course[prog_id][(seq, full_name)] = row['n']

    # Merge course keys from both sources so every course appears even if no current learners
    _all_course_keys: dict = defaultdict(set)
    for prog_id, courses in _course_health.items():
        _all_course_keys[prog_id].update(courses.keys())
    for prog_id, courses in _badges_by_course.items():
        _all_course_keys[prog_id].update(courses.keys())

    course_prog_by_prog: dict = defaultdict(list)
    for prog_id, keys in _all_course_keys.items():
        for (seq, full_name) in sorted(keys):
            by_health = dict(_course_health[prog_id].get((seq, full_name), {}))
            badges = _badges_by_course[prog_id].get((seq, full_name), 0)
            course_prog_by_prog[prog_id].append({
                'seq': seq,
                'label': full_name,
                'by_health': by_health,
                'badges': badges,
                'total': sum(by_health.values()),
            })

    # Enrollment timeline — effective start: MAX(best learner date, programme start)
    # Anyone who enrolled before the programme launched is counted from launch week.
    _learner_d = Coalesce('enrolment_date', 'activation_date', 'programme__start_date', 'first_sign_of_life_date')
    _prog_d    = Coalesce('programme__start_date', 'enrolment_date', 'activation_date', 'first_sign_of_life_date')
    _enrol_date_rows = list(
        Enrolment.objects
        .filter(programme_id__in=prog_pks, has_activity_data=True)
        .exclude(learner__payment_status='unknown')
        .annotate(_d=Greatest(_learner_d, _prog_d))
        .exclude(_d__isnull=True)
        .values_list('programme_id', '_d')
    )

    _timeline_raw: dict = defaultdict(lambda: defaultdict(int))
    for prog_id, d in _enrol_date_rows:
        monday = d - _timedelta(days=d.weekday())
        _timeline_raw[monday.isoformat()][prog_id] += 1

    sorted_weeks = sorted(_timeline_raw.keys())
    _prog_colors = ['#0452F0','#0d9488','#f97316','#ec4899','#ef4444','#06b6d4','#a855f7']
    timeline_datasets = [
        {
            'label': prog.code,
            'data': [_timeline_raw[w].get(prog.pk, 0) for w in sorted_weeks],
            'backgroundColor': _prog_colors[i % len(_prog_colors)],
            'borderWidth': 0,
            'borderRadius': 2,
        }
        for i, prog in enumerate(programmes)
    ]
    timeline_labels = [
        '{} – {}'.format(
            _date.fromisoformat(w).strftime('%d %b'),
            (_date.fromisoformat(w) + _timedelta(days=6)).strftime('%d %b'),
        )
        for w in sorted_weeks
    ]

    # Assemble per-programme payload for Chart.js
    chart_data = []
    for prog in programmes:
        h = health_by_prog.get(prog.pk, {})
        mix = prog_mix.get(prog.pk, {'solo': 0, 'multi': 0})
        chart_data.append({
            'pk': prog.pk,
            'code': prog.code,
            'name': prog.name,
            'health': {
                'active':          h.get('active', 0),
                'at_risk':         h.get('at_risk', 0),
                'dormant':         h.get('dormant', 0),
                'graduated':       h.get('graduated', 0),
                'not_yet_started': h.get('not_yet_started', 0),
            },
            'mix': mix,
            'courses': course_prog_by_prog.get(prog.pk, []),
        })

    return render(request, 'selfpaced/_programme_charts.html', {
        'chart_data': {
            'programmes':       chart_data,
            'global_solo':      global_solo,
            'global_multi':     global_multi,
            'timelineLabels':   timeline_labels,
            'timelineDatasets': timeline_datasets,
        },
        'programmes':        programmes,
        'timeline_has_data': bool(sorted_weeks),
    })
