from collections import defaultdict
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from selfpaced.exports import export_enrolments_csv
from selfpaced.models import (
    CourseEnrolment,
    CourseStatus,
    Enrolment,
    Learner,
    PaceStatus,
    Pod,
    PodAssignment,
    Programme,
)
from selfpaced.pace import compute_pod_paces


def _onboarded_exists(learner_field='learner'):
    """Subquery: True if the learner has graduated a prerequisite programme."""
    return Exists(
        Enrolment.objects.filter(
            learner=OuterRef(learner_field),
            programme__is_prerequisite=True,
            is_graduated=True,
        )
    )


@login_required
def pod_list(request):
    # Filter params
    prog_filter = request.GET.get('programme', '').strip()
    month_filter = request.GET.get('month', '').strip()  # "YYYY-MM"

    # All programmes that have pods, for the filter dropdown
    programmes_with_pods = list(
        Programme.objects
        .filter(pods__isnull=False, is_active=True)
        .distinct()
        .order_by('code')
    )

    # All distinct target months, for the filter dropdown
    all_months = list(
        Pod.objects.order_by('target_month')
        .values_list('target_month', flat=True)
        .distinct()
    )

    # Base queryset
    pods_qs = Pod.objects.select_related('programme')

    if prog_filter:
        pods_qs = pods_qs.filter(programme__code=prog_filter)
    if month_filter:
        try:
            year, month = int(month_filter.split('-')[0]), int(month_filter.split('-')[1])
            pods_qs = pods_qs.filter(target_month__year=year, target_month__month=month)
        except (ValueError, IndexError):
            pass

    # Pace counts only — no joins through enrolments to avoid row-multiplication
    pods = list(
        pods_qs
        .annotate(
            total_assigned=Count('assignments', filter=Q(assignments__is_current=True)),
            on_track_count=Count('assignments', filter=Q(
                assignments__is_current=True,
                assignments__pace_status=PaceStatus.ON_TRACK,
            )),
            ahead_count=Count('assignments', filter=Q(
                assignments__is_current=True,
                assignments__pace_status=PaceStatus.AHEAD,
            )),
            behind_count=Count('assignments', filter=Q(
                assignments__is_current=True,
                assignments__pace_status=PaceStatus.BEHIND,
            )),
            sig_behind_count=Count('assignments', filter=Q(
                assignments__is_current=True,
                assignments__pace_status=PaceStatus.SIGNIFICANTLY_BEHIND,
            )),
            completed_count=Count('assignments', filter=Q(
                assignments__is_current=True,
                assignments__pace_status=PaceStatus.COMPLETED,
            )),
        )
        .order_by('programme__code', 'target_month')
    )

    # Onboarded count computed separately to avoid JOIN fan-out inflating the counts above
    onboarded_learner_ids = set(
        Enrolment.objects.filter(
            programme__is_prerequisite=True,
            is_graduated=True,
        ).values_list('learner_id', flat=True)
    )
    pod_pks = [p.pk for p in pods]
    onboarded_by_pod: dict = defaultdict(int)
    for pod_pk, _learner_pk in (
        PodAssignment.objects
        .filter(pod_id__in=pod_pks, is_current=True, learner_id__in=onboarded_learner_ids)
        .values_list('pod_id', 'learner_id')
        .distinct()
    ):
        onboarded_by_pod[pod_pk] += 1

    for pod in pods:
        pod.onboarded_count = onboarded_by_pod.get(pod.pk, 0)

    return render(request, 'selfpaced/pod_list.html', {
        'pods': pods,
        'programmes_with_pods': programmes_with_pods,
        'all_months': all_months,
        'prog_filter': prog_filter,
        'month_filter': month_filter,
    })


@login_required
def pod_detail(request, pk):
    pod = get_object_or_404(Pod, pk=pk)

    assignments = list(
        PodAssignment.objects
        .filter(pod=pod, is_current=True)
        .select_related('learner', 'programme')
        .annotate(is_onboarded=_onboarded_exists('learner_id'))
        .order_by('pace_status', 'learner__last_name')
    )

    if request.GET.get('export') == 'csv':
        enrolment_qs = Enrolment.objects.filter(
            learner__in=[a.learner_id for a in assignments],
            programme=pod.programme,
        )
        return export_enrolments_csv(enrolment_qs)

    learner_ids = [a.learner_id for a in assignments]

    # Fetch all CourseEnrolments for these learners in this programme in one query
    all_ces = list(
        CourseEnrolment.objects
        .filter(
            enrolment__learner_id__in=learner_ids,
            enrolment__programme=pod.programme,
            status__in=[CourseStatus.IN_PROGRESS, CourseStatus.COMPLETED],
        )
        .select_related('course')
        .values('enrolment__learner_id', 'status', 'course_id',
                'course__sequence_number', 'course__code', 'course__full_name',
                'course__programme_id')
        .order_by('enrolment__learner_id', 'course__sequence_number')
    )

    # Build per-learner lookups
    completed_counts: dict = defaultdict(int)
    # current = highest in_progress course; fallback to highest completed
    current_course_by_learner: dict = {}
    last_completed_by_learner: dict = {}

    for row in all_ces:
        lid = row['enrolment__learner_id']
        if row['status'] == CourseStatus.COMPLETED:
            completed_counts[lid] += 1
            last_completed_by_learner[lid] = row  # last completed (ordered by seq)
        elif row['status'] == CourseStatus.IN_PROGRESS:
            current_course_by_learner[lid] = row

    # Fill gaps: learners with no in_progress get their last completed course
    for lid, row in last_completed_by_learner.items():
        if lid not in current_course_by_learner:
            current_course_by_learner[lid] = row

    # Non-onboarded learners who are enrolled in WALX → show "WALX" as current course
    non_onboarded_ids = [a.learner_id for a in assignments if not a.is_onboarded]
    in_walx_ids = set(
        Enrolment.objects
        .filter(learner_id__in=non_onboarded_ids, programme__is_prerequisite=True)
        .values_list('learner_id', flat=True)
    ) if non_onboarded_ids else set()

    # Attach to assignments
    for a in assignments:
        a.courses_completed_count = completed_counts.get(a.learner_id, 0)
        a.current_course_row = current_course_by_learner.get(a.learner_id)
        a.in_walx = a.learner_id in in_walx_ids

    pace_counts = {
        'on_track': sum(1 for a in assignments if a.pace_status == PaceStatus.ON_TRACK),
        'ahead':    sum(1 for a in assignments if a.pace_status == PaceStatus.AHEAD),
        'behind':   sum(1 for a in assignments if a.pace_status == PaceStatus.BEHIND),
        'sig_behind': sum(1 for a in assignments if a.pace_status == PaceStatus.SIGNIFICANTLY_BEHIND),
        'completed': sum(1 for a in assignments if a.pace_status == PaceStatus.COMPLETED),
        'total': len(assignments),
    }

    return render(request, 'selfpaced/pod_detail.html', {
        'pod': pod,
        'assignments': assignments,
        'pace_counts': pace_counts,
    })


def _do_pod_assignment(learner, programme, pod, user):
    """
    Assign a learner to a pod for a given programme.
    If a current assignment already exists for this programme, it is superseded.
    Returns (created: bool, switched_from: Pod | None).
    """
    existing = PodAssignment.objects.filter(
        learner=learner, programme=programme, is_current=True
    ).first()

    if existing and existing.pod_id == pod.pk:
        return False, None  # already in this pod, no-op

    switched_from = None
    if existing:
        switched_from = existing.pod
        existing.is_current = False
        existing.pod_switch_date = date.today()
        existing.pod_switch_reason = (
            f'Manually reassigned by {user.get_full_name() or user.username}'
        )
        existing.switch_logged_by = user
        existing.save(update_fields=[
            'is_current', 'pod_switch_date', 'pod_switch_reason', 'switch_logged_by',
        ])

    PodAssignment.objects.create(
        learner=learner,
        programme=programme,
        pod=pod,
        method='admin_assigned',
        is_current=True,
        previous_pod=switched_from,
    )
    return True, switched_from


@login_required
@require_POST
def assign_to_pod(request, pk):
    """Assign a learner (by email) to this pod. Called from the pod detail page."""
    pod = get_object_or_404(Pod, pk=pk)
    email = request.POST.get('email', '').strip().lower()
    if not email:
        messages.error(request, 'Email address is required.')
        return redirect('sp_pod_detail', pk=pk)

    try:
        learner = Learner.objects.get(email=email)
    except Learner.DoesNotExist:
        messages.error(request, f'No learner found with email "{email}".')
        return redirect('sp_pod_detail', pk=pk)

    if not Enrolment.objects.filter(learner=learner, programme=pod.programme).exists():
        messages.error(
            request,
            f'{learner.full_name or email} is not enrolled in {pod.programme.code}.',
        )
        return redirect('sp_pod_detail', pk=pk)

    created, switched_from = _do_pod_assignment(learner, pod.programme, pod, request.user)
    if not created:
        messages.info(request, f'{learner.full_name or email} is already in this pod.')
    elif switched_from:
        messages.success(
            request,
            f'{learner.full_name or email} moved from {switched_from.name} to {pod.name}.',
        )
    else:
        messages.success(request, f'{learner.full_name or email} assigned to {pod.name}.')
    return redirect('sp_pod_detail', pk=pk)


@login_required
@require_POST
def remove_from_pod(request, assignment_pk):
    """Remove a learner from their current pod assignment."""
    assignment = get_object_or_404(PodAssignment, pk=assignment_pk, is_current=True)
    pod_pk = assignment.pod_id
    label = assignment.learner.full_name or assignment.learner.email
    assignment.is_current = False
    assignment.save(update_fields=['is_current'])
    messages.success(request, f'{label} removed from pod.')
    return redirect('sp_pod_detail', pk=pod_pk)


@login_required
@require_POST
def assign_learner_pod(request, email):
    """Assign/switch a learner's pod from the learner profile page."""
    learner = get_object_or_404(Learner, pk=email)
    pod_pk = request.POST.get('pod_pk', '').strip()
    programme_pk = request.POST.get('programme_pk', '').strip()

    if not pod_pk or not programme_pk:
        messages.error(request, 'Both pod and programme are required.')
        return redirect('sp_learner_profile', email=email)

    pod = get_object_or_404(Pod, pk=pod_pk, programme_id=programme_pk)
    programme = pod.programme

    if not Enrolment.objects.filter(learner=learner, programme=programme).exists():
        messages.error(request, f'Learner is not enrolled in {programme.code}.')
        return redirect('sp_learner_profile', email=email)

    created, switched_from = _do_pod_assignment(learner, programme, pod, request.user)
    if not created:
        messages.info(request, f'Already assigned to {pod.name}.')
    elif switched_from:
        messages.success(request, f'Moved from {switched_from.name} to {pod.name}.')
    else:
        messages.success(request, f'Assigned to {pod.name}.')
    return redirect('sp_learner_profile', email=email)


@login_required
@require_POST
def recompute_pod_pace(request, pk):
    pod = get_object_or_404(Pod, pk=pk)
    compute_pod_paces(pod)
    return redirect('sp_pod_detail', pk=pod.pk)


@login_required
@require_POST
def recompute_all_pod_paces(request):
    for pod in Pod.objects.filter(status='active'):
        compute_pod_paces(pod)
    return redirect('sp_pod_list')
