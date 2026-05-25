from datetime import date as _date

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from selfpaced.models import (
    Assignment, AssignmentProgress, Course, CourseEnrolment,
    Enrolment, Programme, ProgrammeThreshold, THRESHOLD_DEFAULTS,
)


@login_required
def programme_admin_list(request):
    programmes = (
        Programme.objects
        .annotate(
            course_count=Count('courses', filter=Q(courses__is_active=True), distinct=True),
            enrolment_count=Count('enrolments', distinct=True),
            active_count=Count('enrolments', filter=Q(enrolments__health_status='active'), distinct=True),
            at_risk_count=Count('enrolments', filter=Q(enrolments__health_status='at_risk'), distinct=True),
            dormant_count=Count('enrolments', filter=Q(enrolments__health_status='dormant'), distinct=True),
            graduated_count=Count('enrolments', filter=Q(enrolments__health_status='graduated'), distinct=True),
            not_started_count=Count('enrolments', filter=Q(enrolments__health_status='not_yet_started'), distinct=True),
        )
        .prefetch_related('threshold')
        .order_by('code')
    )
    return render(request, 'selfpaced/admin/programme_list.html', {
        'programmes': programmes,
    })


@login_required
def programme_admin_edit(request, pk):
    programme = get_object_or_404(Programme, pk=pk)
    threshold, _ = ProgrammeThreshold.objects.get_or_create(programme=programme)

    errors = {}
    merge_error = request.GET.get('merge_error', '')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            errors['name'] = 'Name is required.'

        threshold_fields = [
            'activation_threshold_days',
            'inactivity_threshold_days',
            'dormancy_threshold_days',
            'stuck_assignment_threshold_days',
            'pass_rate_threshold_pct',
            'inter_course_threshold_days',
        ]
        threshold_values = {}
        for field in threshold_fields:
            raw = request.POST.get(field, '').strip()
            if raw == '':
                threshold_values[field] = None
            else:
                try:
                    threshold_values[field] = int(raw)
                    if threshold_values[field] < 0:
                        raise ValueError
                except ValueError:
                    errors[field] = 'Must be a positive integer or blank (use default).'

        # Parse start_date / end_date
        start_date = None
        end_date = None
        raw_start = request.POST.get('start_date', '').strip()
        raw_end = request.POST.get('end_date', '').strip()
        if raw_start:
            try:
                start_date = _date.fromisoformat(raw_start)
            except ValueError:
                errors['start_date'] = 'Enter a valid date (YYYY-MM-DD).'
        if raw_end:
            try:
                end_date = _date.fromisoformat(raw_end)
            except ValueError:
                errors['end_date'] = 'Enter a valid date (YYYY-MM-DD).'
        if start_date and end_date and end_date < start_date:
            errors['end_date'] = 'End date must be on or after start date.'

        if not errors:
            programme.name = name
            programme.is_active = 'is_active' in request.POST
            programme.awards_credentials = 'awards_credentials' in request.POST
            programme.awards_certificate = 'awards_certificate' in request.POST
            programme.is_prerequisite = 'is_prerequisite' in request.POST
            ehub_code = request.POST.get('ehub_code', '').strip().upper() or None
            programme.ehub_code = ehub_code
            programme.start_date = start_date
            programme.end_date = end_date
            programme.save(update_fields=[
                'name', 'is_active', 'awards_credentials', 'awards_certificate',
                'is_prerequisite', 'ehub_code', 'start_date', 'end_date',
            ])

            for field, val in threshold_values.items():
                setattr(threshold, field, val)
            threshold.updated_by = request.user
            threshold.save(update_fields=threshold_fields + ['updated_by', 'updated_at'])

            return redirect('sp_admin_programme_list')

    courses = (
        Course.objects
        .filter(programme=programme)
        .annotate(
            enrolment_count=Count('course_enrolments', distinct=True),
            assignment_count=Count('assignments', distinct=True),
        )
        .order_by('sequence_number')
    )
    enrolment_count = Enrolment.objects.filter(programme=programme).count()

    threshold_config = [
        {
            'field': 'activation_threshold_days',
            'label': 'Activation threshold',
            'hint': 'Days from FSOL before "never activated" flag fires',
            'unit': 'days',
            'default': THRESHOLD_DEFAULTS.get('activation_threshold_days'),
        },
        {
            'field': 'inactivity_threshold_days',
            'label': 'Inactivity threshold',
            'hint': 'Days without activity before "inactive" flag fires',
            'unit': 'days',
            'default': THRESHOLD_DEFAULTS.get('inactivity_threshold_days'),
        },
        {
            'field': 'dormancy_threshold_days',
            'label': 'Dormancy threshold',
            'hint': 'Days inactive before status becomes Dormant',
            'unit': 'days',
            'default': THRESHOLD_DEFAULTS.get('dormancy_threshold_days'),
        },
        {
            'field': 'stuck_assignment_threshold_days',
            'label': 'Stuck on assignment threshold',
            'hint': 'Days accessed but not submitted before "stuck" flag fires',
            'unit': 'days',
            'default': THRESHOLD_DEFAULTS.get('stuck_assignment_threshold_days'),
        },
        {
            'field': 'pass_rate_threshold_pct',
            'label': 'Low pass rate threshold',
            'hint': 'Pass rate below this % triggers "low pass rate" flag',
            'unit': '%',
            'default': THRESHOLD_DEFAULTS.get('pass_rate_threshold_pct'),
        },
        {
            'field': 'inter_course_threshold_days',
            'label': 'Inter-course stall threshold',
            'hint': 'Days between completing one course and starting the next before "stalled" flag fires',
            'unit': 'days',
            'default': THRESHOLD_DEFAULTS.get('inter_course_threshold_days'),
        },
    ]

    return render(request, 'selfpaced/admin/programme_edit.html', {
        'programme': programme,
        'threshold': threshold,
        'threshold_config': threshold_config,
        'courses': courses,
        'enrolment_count': enrolment_count,
        'errors': errors,
        'merge_error': merge_error,
        'post': request.POST if errors else {},
    })


@login_required
def course_edit(request, prog_pk, course_pk):
    programme = get_object_or_404(Programme, pk=prog_pk)
    course = get_object_or_404(Course, pk=course_pk, programme=programme)
    errors = {}

    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        seq_raw = request.POST.get('sequence_number', '').strip()
        dur_raw = request.POST.get('expected_duration_days', '').strip()
        is_active = 'is_active' in request.POST

        if not full_name:
            errors['full_name'] = 'Name is required.'

        seq = None
        try:
            seq = int(seq_raw)
            if seq < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors['sequence_number'] = 'Must be a non-negative integer.'

        if seq is not None:
            conflict = Course.objects.filter(
                programme=programme, sequence_number=seq
            ).exclude(pk=course_pk).first()
            if conflict:
                errors['sequence_number'] = (
                    f'Sequence {seq} is already used by "{conflict.full_name}". '
                    f'Use Merge if these are the same course.'
                )

        dur = None
        if dur_raw:
            try:
                dur = int(dur_raw)
                if dur <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                errors['expected_duration_days'] = 'Must be a positive integer or leave blank.'

        if not errors:
            course.full_name = full_name
            course.code = code
            course.sequence_number = seq
            course.expected_duration_days = dur
            course.is_active = is_active
            course.save(update_fields=[
                'full_name', 'code', 'sequence_number',
                'expected_duration_days', 'is_active',
            ])
            return redirect('sp_admin_programme_edit', pk=prog_pk)

    enrolment_count = CourseEnrolment.objects.filter(course=course).count()
    assignment_count = Assignment.objects.filter(course=course).count()
    other_courses = Course.objects.filter(programme=programme).exclude(pk=course_pk).order_by('sequence_number')

    return render(request, 'selfpaced/admin/course_edit.html', {
        'programme': programme,
        'course': course,
        'enrolment_count': enrolment_count,
        'assignment_count': assignment_count,
        'other_courses': other_courses,
        'errors': errors,
        'post': request.POST if errors else {},
    })


@login_required
def course_merge(request, prog_pk):
    """Merge source course into target. Moves enrolments and assignments, then deactivates source."""
    programme = get_object_or_404(Programme, pk=prog_pk)

    if request.method != 'POST':
        return redirect('sp_admin_programme_edit', pk=prog_pk)

    source_pk = request.POST.get('source_pk', '').strip()
    target_pk = request.POST.get('target_pk', '').strip()

    if not source_pk or not target_pk:
        return redirect('sp_admin_programme_edit', pk=prog_pk)

    if source_pk == target_pk:
        url = reverse('sp_admin_programme_edit', args=[prog_pk])
        return redirect(f'{url}?merge_error=Cannot+merge+a+course+into+itself')

    source = get_object_or_404(Course, pk=source_pk, programme=programme)
    target = get_object_or_404(Course, pk=target_pk, programme=programme)

    with transaction.atomic():
        # Move assignments: skip if target already has one with the same name
        existing_target_names = set(
            Assignment.objects.filter(course=target).values_list('name', flat=True)
        )
        for assign in Assignment.objects.filter(course=source):
            if assign.name not in existing_target_names:
                assign.course = target
                assign.save(update_fields=['course'])
            # else: duplicate assignment — leave on source (it will become inactive)

        # Move course enrolments
        for ce in CourseEnrolment.objects.filter(course=source).select_related('enrolment'):
            target_ce = CourseEnrolment.objects.filter(
                enrolment=ce.enrolment, course=target
            ).first()
            if target_ce:
                # Learner already has the target course — migrate their progress records
                AssignmentProgress.objects.filter(
                    course_enrolment=ce
                ).update(course_enrolment=target_ce)
                ce.delete()
            else:
                ce.course = target
                ce.save(update_fields=['course'])

        source.is_active = False
        source.save(update_fields=['is_active'])

    return redirect('sp_admin_programme_edit', pk=prog_pk)
