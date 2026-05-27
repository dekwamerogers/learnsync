from collections import defaultdict
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce, Greatest
from django.shortcuts import render

from django.db.models import Min

from selfpaced.models import Course, CourseEnrolment, Enrolment, EnrolmentSnapshot, HealthStatus, IngestionJob, Intervention, Programme
from selfpaced.querysets import activity_learners_qs, real_learners_qs
from selfpaced.utils import safe_json


def _learner_health_from_snapshots(snapshot_qs):
    """
    Derive per-learner overall health from enrolment snapshots using active-wins logic.
    Returns a dict: {status_str: count}.
    """
    by_learner = defaultdict(list)
    for snap in snapshot_qs.values('learner_id', 'health_status'):
        by_learner[snap['learner_id']].append(snap['health_status'])

    counts: dict = defaultdict(int)
    for statuses_list in by_learner.values():
        non_grad = [s for s in statuses_list if s != 'graduated']
        if not non_grad:
            overall = 'graduated'
        else:
            s_set = set(non_grad)
            if 'active' in s_set:
                overall = 'active'
            elif 'at_risk' in s_set:
                overall = 'at_risk'
            elif 'dormant' in s_set:
                overall = 'dormant'
            else:
                overall = 'not_yet_started'
        counts[overall] += 1
    return counts


@login_required
def home(request):
    today = date.today()

    # activity_qs: learners who have appeared in at least one activity CSV — used for all metric denominators.
    # real_qs: all non-WALX-only paid learners — used for total enrolled count (wider set).
    real_qs     = real_learners_qs().exclude(payment_status='unknown')
    activity_qs = activity_learners_qs().exclude(payment_status='unknown')
    total_learners = activity_qs.count()

    # Learner-level health counts — from activity learners only (those in eHub activity export).
    _lh = {r['overall_health_status']: r['n']
           for r in activity_qs.values('overall_health_status').annotate(n=Count('email'))}
    health_counts = {
        'dormant':         _lh.get('dormant', 0),
        'at_risk':         _lh.get('at_risk', 0),
        'active':          _lh.get('active', 0),
        'graduated':       _lh.get('graduated', 0),
        'not_yet_started': _lh.get('not_yet_started', 0),
    }

    # Enrolled-but-not-yet-reached: in roster CSV but never appeared in activity CSV.
    enrolled_not_reached = (
        real_qs.count() - activity_qs.count()
    )

    # Enrolment-level counts — exclude prerequisite programmes, unpaid learners,
    # and enrolments with no activity data (enrollment-only rows).
    real_enrolments = (
        Enrolment.objects
        .filter(has_activity_data=True)
        .exclude(programme__is_prerequisite=True)
        .exclude(learner__payment_status='unknown')
    )

    # Upcoming = programme has a future start_date (hasn't begun yet)
    upcoming_filter = Q(programme__start_date__isnull=False) & Q(programme__start_date__gt=today)
    started_filter  = Q(programme__start_date__isnull=True) | Q(programme__start_date__lte=today)

    started_enrolments  = real_enrolments.filter(started_filter)
    upcoming_enrolments = real_enrolments.filter(upcoming_filter)
    upcoming_enrolment_count = upcoming_enrolments.count()

    _eh = {r['health_status']: r['n']
           for r in started_enrolments.values('health_status').annotate(n=Count('id'))}
    enrolment_counts = {
        'total':           sum(_eh.values()),
        'active':          _eh.get('active', 0),
        'at_risk':         _eh.get('at_risk', 0),
        'dormant':         _eh.get('dormant', 0),
        'graduated':       _eh.get('graduated', 0),
        'not_yet_started': _eh.get('not_yet_started', 0),
    }

    # ── Graduated count for grad rate denominator ─────────────────────────
    graduated_count = health_counts['graduated']

    # ── Module activation: passed the first course of any substantive programme ─
    # ── Retention: passed Module 1 AND enrolment is still active/at_risk/graduated ─
    # ── Graduation rate: graduated ÷ module_activated (of those who started, how many finished) ─
    _active_prog_pks = list(
        Programme.objects.filter(is_active=True, is_prerequisite=False).values_list('pk', flat=True)
    )
    _min_seq = dict(
        Course.objects
        .filter(is_active=True, programme_id__in=_active_prog_pks)
        .exclude(code='WALX')   # WALX completions live on the standalone WALX enrolment, not the main programme enrolment
        .values('programme_id')
        .annotate(ms=Min('sequence_number'))
        .values_list('programme_id', 'ms')
    )
    module_activated_count = 0
    module_retained_count  = 0
    if _min_seq:
        act_q = ret_q = None
        for prog_id, ms in _min_seq.items():
            # Activated: passed Module 1 (is_passed=True on lowest-sequence course)
            cond_a = Q(
                enrolment__programme_id=prog_id,
                course__sequence_number=ms,
                is_passed=True,
            )
            # Retained: passed Module 1 AND enrolment health is active/at_risk/graduated
            # (i.e. they haven't gone dormant or not_yet_started after passing it)
            cond_r = Q(
                enrolment__programme_id=prog_id,
                course__sequence_number=ms,
                is_passed=True,
                enrolment__health_status__in=['active', 'at_risk', 'graduated'],
            )
            act_q = cond_a if act_q is None else act_q | cond_a
            ret_q = cond_r if ret_q is None else ret_q | cond_r

        module_activated_count = (
            CourseEnrolment.objects.filter(act_q)
            .filter(enrolment__has_activity_data=True)
            .exclude(enrolment__learner__payment_status='unknown')
            .values('enrolment__learner_id').distinct().count()
        )
        module_retained_count = (
            CourseEnrolment.objects.filter(ret_q)
            .filter(enrolment__has_activity_data=True)
            .exclude(enrolment__learner__payment_status='unknown')
            .values('enrolment__learner_id').distinct().count()
        )

    module_activation_rate = round(module_activated_count / total_learners * 100) if total_learners else None
    retention_rate         = round(module_retained_count / module_activated_count * 100) if module_activated_count else None

    # Graduation rate: of learners who passed Module 1, how many graduated?
    grad_rate = round(graduated_count / module_activated_count * 100) if module_activated_count else None

    badges_count = (
        Enrolment.objects
        .filter(programme__awards_credentials=True, programme__is_prerequisite=False,
                course_enrolments__status='completed')
        .exclude(learner__payment_status='unknown')
        .values('learner_id')
        .distinct()
        .count()
    )

    certificates_count = (
        Enrolment.objects
        .filter(programme__awards_certificate=True, programme__is_prerequisite=False,
                is_graduated=True)
        .exclude(learner__payment_status='unknown')
        .count()
    )

    # Unique learners who hold at least one certificate — used as the "graduated" badge count
    certified_learner_count = (
        Enrolment.objects
        .filter(programme__awards_certificate=True, programme__is_prerequisite=False,
                is_graduated=True)
        .exclude(learner__payment_status='unknown')
        .values('learner_id')
        .distinct()
        .count()
    )

    # Learners who completed onboarding (graduated any prerequisite programme, e.g. WALX) — paid only
    onboarded_count = (
        Enrolment.objects
        .filter(programme__is_prerequisite=True, is_graduated=True)
        .exclude(learner__payment_status='unknown')
        .values('learner_id')
        .distinct()
        .count()
    )

    # Learners currently in the onboarding programme but not yet graduated — paid only
    currently_onboarding_count = (
        Enrolment.objects
        .filter(programme__is_prerequisite=True, is_graduated=False)
        .exclude(learner__payment_status='unknown')
        .values('learner_id')
        .distinct()
        .count()
    )

    onboarded_rate  = round(onboarded_count / total_learners * 100) if total_learners else None
    onboarding_rate = round(currently_onboarding_count / total_learners * 100) if total_learners else None

    # Per-programme activated enrolment IDs (passed first module of their programme)
    _prog_activated_ids = frozenset(
        CourseEnrolment.objects
        .filter(
            enrolment__in=started_enrolments,
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

    # Roster-only enrolments per programme (in enrolment CSV but NOT in activity CSV)
    _roster_only_counts = dict(
        Enrolment.objects
        .filter(
            has_activity_data=False,
            programme__is_active=True,
            programme__is_prerequisite=False,
        )
        .filter(started_filter)
        .exclude(learner__payment_status='unknown')
        .values('programme__code')
        .annotate(n=Count('pk'))
        .values_list('programme__code', 'n')
    )

    # Per-programme health breakdown for bar chart — paid learners, started programmes
    _prog_rows = list(
        started_enrolments
        .values('programme__id', 'programme__code')
        .annotate(
            graduated=Count('pk', filter=Q(health_status='graduated')),
            active=Count('pk', filter=Q(health_status='active')),
            at_risk=Count('pk', filter=Q(health_status='at_risk')),
            dormant=Count('pk', filter=Q(health_status='dormant')),
            not_yet_started=Count('pk', filter=Q(health_status='not_yet_started')),
            activated=Count('pk', filter=Q(pk__in=_prog_activated_ids)),
        )
        .order_by('programme__code')
    )
    chart_programme_labels  = safe_json([r['programme__code']    for r in _prog_rows])
    chart_graduated         = safe_json([r['graduated']          for r in _prog_rows])
    chart_active            = safe_json([r['active']             for r in _prog_rows])
    chart_at_risk           = safe_json([r['at_risk']            for r in _prog_rows])
    chart_dormant           = safe_json([r['dormant']            for r in _prog_rows])
    chart_not_yet_started   = safe_json([r['not_yet_started']    for r in _prog_rows])

    # Flag breakdown — started programmes only
    _FLAG_ORDER = [
        ('inactive',               'Inactive'),
        ('never_activated',        'Never Activated'),
        ('stuck_on_assignment',    'Stuck on Assignment'),
        ('low_pass_rate',          'Low Pass Rate'),
        ('stalled_between_courses', 'Stalled Between Courses'),
        ('stalled_progression',    'No Onward Progress'),
        ('payment_issue',          'Payment Issue'),
    ]
    _flag_learners: dict = defaultdict(set)
    for row in (
        started_enrolments
        .filter(health_status__in=[HealthStatus.AT_RISK, HealthStatus.DORMANT])
        .values('learner_id', 'active_flags')
    ):
        for flag in (row['active_flags'] or []):
            _flag_learners[flag].add(row['learner_id'])
    chart_flag_labels = safe_json([label for _, label in _FLAG_ORDER])
    chart_flag_counts = safe_json([len(_flag_learners.get(code, set())) for code, _ in _FLAG_ORDER])


    follow_up_count = Intervention.objects.filter(
        follow_up_required=True, follow_up_date__lte=today
    ).count()

    week_ago = today - timedelta(days=7)
    new_this_week_count = (
        real_qs
        .filter(enrolments__enrolment_date__gte=week_ago)
        .distinct()
        .count()
    )

    recent_jobs = list(
        IngestionJob.objects.filter(status='complete').order_by('-uploaded_at')[:2]
    )
    last_job = recent_jobs[0] if recent_jobs else None
    prev_job = recent_jobs[1] if len(recent_jobs) >= 2 else None

    data_stale = False
    if last_job:
        age_days = (today - last_job.uploaded_at.date()).days
        data_stale = age_days >= 7
    else:
        data_stale = True

    # ── Deltas vs previous upload ─────────────────────────────────────────
    deltas = None
    if prev_job:
        prev_snaps = (
            EnrolmentSnapshot.objects
            .filter(ingestion_job=prev_job)
            .exclude(programme__is_prerequisite=True)
            .exclude(payment_status='unknown')
        )

        # Enrolment-level previous counts
        prev_enrolment_health = {
            row['health_status']: row['n']
            for row in prev_snaps.values('health_status').annotate(n=Count('id'))
        }
        prev_total_enrolments = sum(prev_enrolment_health.values())

        # Learner-level previous counts (active-wins rollup over snapshots)
        prev_learner_health = _learner_health_from_snapshots(prev_snaps)
        prev_total_learners  = sum(prev_learner_health.values())
        prev_not_started     = prev_learner_health.get('not_yet_started', 0)
        prev_graduated       = prev_learner_health.get('graduated', 0)
        prev_activated       = prev_total_learners - prev_not_started
        prev_activation_rate = round(prev_activated / prev_total_learners * 100) if prev_total_learners else None
        prev_grad_rate       = round(prev_graduated / prev_activated * 100) if prev_activated else None

        prev_badges = (
            EnrolmentSnapshot.objects
            .filter(ingestion_job=prev_job, programme__awards_credentials=True, courses_completed__gt=0)
            .values('learner_id').distinct().count()
        )
        prev_certs = (
            EnrolmentSnapshot.objects
            .filter(ingestion_job=prev_job, programme__awards_certificate=True,
                    health_status=HealthStatus.GRADUATED)
            .count()
        )

        def _d(current, previous):
            if current is None or previous is None:
                return None
            return current - previous

        deltas = {
            'total_learners':       _d(total_learners,             prev_total_learners),
            'total_enrolments':     _d(enrolment_counts['total'],  prev_total_enrolments),
            # Learner-level health deltas
            'active':               _d(health_counts['active'],         prev_learner_health.get('active', 0)),
            'at_risk':              _d(health_counts['at_risk'],        prev_learner_health.get('at_risk', 0)),
            'dormant':              _d(health_counts['dormant'],        prev_learner_health.get('dormant', 0)),
            'graduated':            _d(health_counts['graduated'],      prev_learner_health.get('graduated', 0)),
            'not_yet_started':      _d(health_counts['not_yet_started'], prev_learner_health.get('not_yet_started', 0)),
            # Enrolment-level health deltas
            'enrolment_active':     _d(enrolment_counts['active'],    prev_enrolment_health.get('active', 0)),
            'enrolment_at_risk':    _d(enrolment_counts['at_risk'],   prev_enrolment_health.get('at_risk', 0)),
            'enrolment_dormant':    _d(enrolment_counts['dormant'],   prev_enrolment_health.get('dormant', 0)),
            'enrolment_graduated':  _d(enrolment_counts['graduated'], prev_enrolment_health.get('graduated', 0)),
            'module_activation_rate': _d(module_activation_rate, prev_activation_rate),
            'grad_rate':            _d(grad_rate,         prev_grad_rate),
            'badges':               _d(badges_count,      prev_badges),
            'certs':                _d(certificates_count, prev_certs),
        }

    # ── Attention-section helpers ─────────────────────────────────────────
    critical_active_drop = False
    active_drop_pct      = None
    if deltas and deltas.get('active') is not None and deltas['active'] < 0:
        prev_active = health_counts['active'] - deltas['active']   # e.g. 175 - (-228) = 403
        if prev_active > 0:
            active_drop_pct      = round(abs(deltas['active']) / prev_active * 100)
            critical_active_drop = active_drop_pct >= 10           # ≥10 % drop is critical

    not_started_pct = (
        round(health_counts['not_yet_started'] / total_learners * 100)
        if total_learners else None
    )

    prog_health_rows = []
    for r in _prog_rows:
        total     = r['graduated'] + r['active'] + r['at_risk'] + r['dormant'] + r['not_yet_started']
        activated = r.get('activated', 0)
        roster_only = _roster_only_counts.get(r['programme__code'], 0)
        prog_health_rows.append({
            'code':            r['programme__code'],
            'pk':              r['programme__id'],
            'graduated':       r['graduated'],
            'active':          r['active'],
            'at_risk':         r['at_risk'],
            'dormant':         r['dormant'],
            'not_started':     r['not_yet_started'],
            'total':           total,
            'activated':       activated,
            'roster_only':     roster_only,
            # Rates (% of activity-data enrolments)
            'activation_rate': round(activated         / total * 100, 1) if total else 0,
            'active_rate':     round(r['active']       / total * 100, 1) if total else 0,
            'at_risk_rate':    round(r['at_risk']      / total * 100, 1) if total else 0,
            'dormant_rate':    round(r['dormant']      / total * 100, 1) if total else 0,
            'graduated_rate':  round(r['graduated']    / total * 100, 1) if total else 0,
        })

    low_engagement_progs = [
        r['code'] for r in prog_health_rows
        if r['total'] > 10 and
           (r['active'] + r['at_risk'] + r['dormant'] + r['graduated']) < r['total'] * 0.15
    ]

    # ── Weekly enrolment timeline ─────────────────────────────────────────
    _prog_colors = ['#0452F0','#0d9488','#f97316','#ec4899','#ef4444','#06b6d4','#a855f7']
    active_progs = list(
        Programme.objects.filter(is_active=True, is_prerequisite=False).order_by('code')
    )
    prog_pks = [p.pk for p in active_progs]

    _enrol_rows = list(
        Enrolment.objects
        .filter(programme_id__in=prog_pks)
        .exclude(programme__is_prerequisite=True)
        .exclude(learner__payment_status='unknown')
        .annotate(_d=Greatest(
            Coalesce('enrolment_date', 'activation_date', 'programme__start_date', 'first_sign_of_life_date'),
            Coalesce('programme__start_date', 'enrolment_date', 'activation_date', 'first_sign_of_life_date'),
        ))
        .exclude(_d__isnull=True)
        .values_list('programme_id', '_d')
    )

    _week_buckets: dict = defaultdict(lambda: defaultdict(int))
    for prog_id, d in _enrol_rows:
        monday = d - timedelta(days=d.weekday())
        _week_buckets[monday.isoformat()][prog_id] += 1

    sorted_weeks = sorted(_week_buckets.keys())
    enrol_timeline_labels = safe_json([
        '{} – {}'.format(
            date.fromisoformat(w).strftime('%d %b'),
            (date.fromisoformat(w) + timedelta(days=6)).strftime('%d %b'),
        )
        for w in sorted_weeks
    ])
    enrol_timeline_datasets = safe_json([
        {
            'label': prog.code,
            'data': [_week_buckets[w].get(prog.pk, 0) for w in sorted_weeks],
            'backgroundColor': _prog_colors[i % len(_prog_colors)],
            'borderWidth': 0,
            'borderRadius': 2,
        }
        for i, prog in enumerate(active_progs)
    ])
    enrol_timeline_has_data = bool(sorted_weeks)

    return render(request, 'selfpaced/home.html', {
        'health_counts':          health_counts,
        'enrolment_counts':       enrolment_counts,
        'total_learners':         total_learners,
        'enrolled_not_reached':   enrolled_not_reached,
        'module_activation_rate':   module_activation_rate,
        'module_activated_count':   module_activated_count,
        'retention_rate':           retention_rate,
        'module_retained_count':    module_retained_count,
        'onboarded_rate':           onboarded_rate,
        'onboarding_rate':          onboarding_rate,
        'grad_rate':                grad_rate,
        'badges_count':        badges_count,
        'certificates_count':  certificates_count,
        'follow_up_count':     follow_up_count,
        'new_this_week_count': new_this_week_count,
        'last_job':            last_job,
        'data_stale':          data_stale,
        'today':               today,
        'deltas':              deltas,
        'chart_programme_labels': chart_programme_labels,
        'chart_graduated':    chart_graduated,
        'chart_active':       chart_active,
        'chart_at_risk':      chart_at_risk,
        'chart_dormant':           chart_dormant,
        'chart_not_yet_started':   chart_not_yet_started,
        'chart_flag_labels':       chart_flag_labels,
        'chart_flag_counts':       chart_flag_counts,
        'upcoming_enrolment_count':  upcoming_enrolment_count,
        'onboarded_count':           onboarded_count,
        'currently_onboarding_count':  currently_onboarding_count,
        'certified_learner_count':   certified_learner_count,
        'enrol_timeline_labels':     enrol_timeline_labels,
        'enrol_timeline_datasets':   enrol_timeline_datasets,
        'enrol_timeline_has_data':   enrol_timeline_has_data,
        'prog_health_rows':          prog_health_rows,
    })
