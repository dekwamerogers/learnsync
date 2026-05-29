import logging
import threading

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from selfpaced.forms import CSVUploadForm
from selfpaced.models import Course, FlaggedRow, IngestionJob, Programme, ProgrammeIdentifierRegistry

logger = logging.getLogger(__name__)


def _run_ingestion_thread(job_pk: int) -> None:
    """Run ingestion in a background thread, isolated from the request connection."""
    from django.db import close_old_connections
    from selfpaced.engine import run_ingestion
    close_old_connections()
    try:
        run_ingestion(job_pk)
    except Exception:
        pass  # engine writes the failure to the job record
    finally:
        close_old_connections()


def _run_preview_thread(job_pk: int) -> None:
    """Run preview_ingestion in a background thread so the upload response is instant."""
    from django.db import close_old_connections
    close_old_connections()
    try:
        from selfpaced.engine import preview_ingestion  # inside try so import errors are caught
        preview_ingestion(job_pk)
        # preview_ingestion sets status → 'pending_review' or 'failed' itself.
    except Exception as exc:
        # Failsafe: if the engine crashes before saving, mark it failed.
        try:
            IngestionJob.objects.filter(pk=job_pk, status='previewing').update(
                status='failed',
                errors=[str(exc)],
            )
        except Exception:
            pass
    finally:
        close_old_connections()


@login_required
def admin_home(request):
    from selfpaced.models import Programme
    recent_jobs = IngestionJob.objects.order_by('-uploaded_at')[:10]
    flagged_count = FlaggedRow.objects.filter(resolution='').count()
    programme_count = Programme.objects.filter(is_active=True).count()

    return render(request, 'selfpaced/admin/home.html', {
        'recent_jobs': recent_jobs,
        'flagged_count': flagged_count,
        'programme_count': programme_count,
    })


@login_required
def upload_csv(request):
    if request.method == 'POST':
        # Guard: reject if a job is already running to prevent accidental spam.
        if IngestionJob.objects.filter(status__in=['previewing', 'pending_review', 'processing']).exists():
            messages.warning(
                request,
                'An ingestion job is already pending review or in progress. '
                'Wait for it to complete or cancel it before uploading again.',
            )
            return redirect('sp_ingestion_log')
        form = CSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            f = form.cleaned_data['file']
            data_as_of_date = form.cleaned_data.get('data_as_of_date')
            job = IngestionJob.objects.create(
                uploaded_by=request.user,
                file_name=f.name,
                file=f,            # stored on disk via FileField — no DB blob
                file_content=b'',  # kept for backward-compat; new jobs don't use it
                status='previewing',
                data_as_of_date=data_as_of_date,
            )
            t = threading.Thread(target=_run_preview_thread, args=(job.pk,), daemon=True)
            t.start()
            return redirect('sp_job_review', pk=job.pk)
    else:
        form = CSVUploadForm()
    return render(request, 'selfpaced/admin/upload.html', {'form': form})


@login_required
def review_job(request, pk):
    """Show a dry-run preview and let the admin confirm or cancel the upload."""
    job = get_object_or_404(IngestionJob, pk=pk)
    if job.status == 'previewing':
        # Preview is still running in the background — render a loading page
        # that HTMX polls until it transitions to pending_review.
        return render(request, 'selfpaced/admin/review.html', {
            'job': job,
            'previewing': True,
        })
    if job.status != 'pending_review':
        return redirect('sp_job_detail', pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'confirm':
            job.status = 'processing'
            job.save(update_fields=['status'])
            t = threading.Thread(
                target=_run_ingestion_thread, args=(job.pk,), daemon=True
            )
            t.start()
            messages.info(request, f'Ingestion started for job #{pk} — the page will update automatically.')
            return redirect('sp_job_detail', pk=pk)
        elif action == 'cancel':
            job.status = 'cancelled'
            job.save(update_fields=['status'])
            messages.info(request, f'Upload #{pk} cancelled — no data was saved.')
            return redirect('sp_ingestion_log')

    review = job.review_data or {}
    needs_programme_list = bool(review.get('flagged_rows')) or bool(review.get('new_programmes'))
    programmes_for_mapping = (
        Programme.objects.filter(is_active=True).prefetch_related('courses').order_by('code')
        if needs_programme_list else []
    )

    # Enrich breakdown with existing courses per programme (for the match dropdown).
    breakdown = review.get('programme_breakdown', [])
    if breakdown:
        codes = [p['code'] for p in breakdown]
        existing_by_code: dict[str, list] = {}
        prog_pk_by_code: dict[str, int] = dict(
            Programme.objects.filter(code__in=codes).values_list('code', 'pk')
        )
        for course in (
            Course.objects
            .filter(programme__code__in=codes, is_active=True)
            .select_related('programme')
            .order_by('programme__code', 'sequence_number')
        ):
            existing_by_code.setdefault(course.programme.code, []).append(course)
        for prog_entry in breakdown:
            prog_entry['existing_courses'] = existing_by_code.get(prog_entry['code'], [])
            prog_entry['prog_pk'] = prog_pk_by_code.get(prog_entry['code'])

    return render(request, 'selfpaced/admin/review.html', {
        'job': job,
        'review': review,
        'programmes_for_mapping': programmes_for_mapping,
    })


@login_required
@require_POST
def retry_job(request, pk):
    """Re-run a pending or failed ingestion job synchronously."""
    job = get_object_or_404(IngestionJob, pk=pk)
    if job.status not in ('pending', 'failed'):
        messages.warning(request, f'Job #{pk} has status "{job.status}" — nothing to retry.')
        return redirect('sp_job_detail', pk=pk)
    job.status = 'processing'
    job.errors = []
    job.warnings = []
    job.rows_processed = 0
    job.new_learners = 0
    job.flagged_row_count = 0
    job.progress_log = []
    job.save(update_fields=['status', 'errors', 'warnings', 'rows_processed',
                            'new_learners', 'flagged_row_count', 'progress_log'])
    t = threading.Thread(target=_run_ingestion_thread, args=(job.pk,), daemon=True)
    t.start()
    messages.info(request, f'Job #{pk} re-queued — the page will update automatically.')
    return redirect('sp_job_detail', pk=pk)


@login_required
@require_POST
def cancel_ingestion_job(request, pk):
    """Request cancellation of an in-progress ingestion job.
    Sets the cancel_requested flag; the background thread picks it up between phases."""
    job = get_object_or_404(IngestionJob, pk=pk)
    if job.status != 'processing':
        messages.warning(request, f'Job #{pk} is not currently processing (status: {job.status}).')
        return redirect('sp_job_detail', pk=pk)
    IngestionJob.objects.filter(pk=pk).update(cancel_requested=True)
    messages.info(request, f'Cancellation requested for job #{pk} — it will stop at the next checkpoint.')
    return redirect('sp_job_detail', pk=pk)


@login_required
@require_POST
def cancel_enrolment_job(request, pk):
    """Request cancellation of an in-progress enrolment upload job."""
    from selfpaced.models import EnrolmentUploadJob
    job = get_object_or_404(EnrolmentUploadJob, pk=pk)
    if job.status != 'processing':
        messages.warning(request, f'Enrolment job #{pk} is not currently processing.')
        return redirect('sp_enrolment_detail', pk=pk)
    EnrolmentUploadJob.objects.filter(pk=pk).update(cancel_requested=True)
    messages.info(request, f'Cancellation requested for enrolment job #{pk}.')
    return redirect('sp_enrolment_detail', pk=pk)


@login_required
@require_POST
def cancel_pod_job(request, pk):
    """Request cancellation of an in-progress POD import job."""
    from selfpaced.models import PodImportJob
    job = get_object_or_404(PodImportJob, pk=pk)
    if job.status != 'processing':
        messages.warning(request, f'Pod import job #{pk} is not currently processing.')
        return redirect('sp_pod_import_detail', pk=pk)
    PodImportJob.objects.filter(pk=pk).update(cancel_requested=True)
    messages.info(request, f'Cancellation requested for pod import job #{pk}.')
    return redirect('sp_pod_import_detail', pk=pk)


@login_required
@require_POST
def delete_job(request, pk):
    """
    Delete an ingestion job and the enrolments/learners it created.
    Updates to existing records made by this job cannot be rolled back.
    """
    from selfpaced.models import Enrolment, Learner

    job = get_object_or_404(IngestionJob, pk=pk)
    if job.status == 'processing':
        messages.error(request, f'Job #{pk} is currently processing and cannot be deleted.')
        return redirect('sp_job_detail', pk=pk)

    created_enrolments = Enrolment.objects.filter(created_by_job=job)
    learner_emails = list(created_enrolments.values_list('learner_id', flat=True))
    enrolment_count = created_enrolments.count()

    with transaction.atomic():
        # Delete enrolments created by this job (cascades CourseEnrolment, AssignmentProgress)
        created_enrolments.delete()
        # Delete any learners who now have zero enrolments
        orphaned = Learner.objects.filter(
            email__in=learner_emails
        ).annotate(remaining=Count('enrolments')).filter(remaining=0)
        learner_count = orphaned.count()
        orphaned.delete()
        # Delete the job (cascades FlaggedRows; SET_NULL on snapshots and created_by_job refs)
        job.delete()

    messages.success(
        request,
        f'Job #{pk} deleted — {enrolment_count} enrolment(s) and '
        f'{learner_count} learner(s) removed.'
    )
    return redirect('sp_ingestion_log')


@login_required
def ingestion_log(request):
    jobs = IngestionJob.objects.order_by('-uploaded_at')
    return render(request, 'selfpaced/admin/ingestion_log.html', {'jobs': jobs})


@login_required
def map_flagged_row(request, pk, row_pk):
    """Allow an admin to map an unrecognised eHub pattern to a programme/course."""
    from django.utils import timezone

    job = get_object_or_404(IngestionJob, pk=pk)
    row = get_object_or_404(FlaggedRow, pk=row_pk, job=job)

    # Extract the eHub class name — raw_data stores the original CSV row dict
    raw = row.raw_data or {}
    ehub_class = raw.get('eHub class name') or raw.get('ehub_class_name', '')

    programmes = Programme.objects.filter(is_active=True).prefetch_related('courses').order_by('code')

    if request.method == 'POST':
        programme_pk = request.POST.get('programme')
        course_pk = request.POST.get('course') or None
        pattern = (request.POST.get('pattern') or ehub_class).strip()

        if not programme_pk or not pattern:
            messages.error(request, 'Please select a programme and confirm the pattern.')
        else:
            programme = get_object_or_404(Programme, pk=programme_pk)
            course = Course.objects.filter(pk=course_pk, programme=programme).first() if course_pk else None

            registry_entry, created = ProgrammeIdentifierRegistry.objects.get_or_create(
                raw_pattern=pattern,
                defaults={
                    'pattern_type': 'ehub_class_name',
                    'programme': programme,
                    'course': course,
                    'created_by': request.user,
                },
            )
            if not created:
                # Update existing entry if it points somewhere different
                updated = False
                if registry_entry.programme != programme:
                    registry_entry.programme = programme
                    updated = True
                if registry_entry.course != course:
                    registry_entry.course = course
                    updated = True
                if updated:
                    registry_entry.save(update_fields=['programme', 'course'])

            row.resolution = 'mapped'
            row.resolved_by = request.user
            row.resolved_at = timezone.now()
            row.save(update_fields=['resolution', 'resolved_by', 'resolved_at'])

            action = 'updated' if not created else 'created'
            messages.success(
                request,
                f'Pattern "{pattern}" {action} → {programme.code}'
                + (f' / {course.full_name}' if course else '') + '.'
            )
            return redirect('sp_job_detail', pk=pk)

    return render(request, 'selfpaced/admin/map_row.html', {
        'job': job,
        'row': row,
        'ehub_class': ehub_class,
        'programmes': programmes,
    })


@login_required
def ingestion_job_detail(request, pk):
    from selfpaced.engine import PIPELINE_STEPS

    job = IngestionJob.objects.get(pk=pk)
    flagged_rows = FlaggedRow.objects.filter(job=job).order_by('id')
    created_enrolment_count = job.created_enrolments.count()

    # Build a merged timeline: static labels + dynamic log entries
    log_by_step = {entry['step']: entry for entry in (job.progress_log or [])}
    steps_done = len(log_by_step)
    pipeline = []
    for i, label in enumerate(PIPELINE_STEPS, start=1):
        entry = log_by_step.get(i)
        pipeline.append({
            'num':        i,
            'label':      label,
            'entry':      entry,                     # None if not yet run
            'is_done':    entry is not None,
            'is_current': not entry and i == steps_done + 1 and job.status == 'processing',
            'is_failed':  not entry and job.status == 'failed' and i == steps_done + 1,
        })

    pct = int(steps_done / len(PIPELINE_STEPS) * 100)

    has_mappable_rows = flagged_rows.filter(flag_reason='unrecognised_pattern', resolution='').exists()
    programmes_for_mapping = (
        Programme.objects.filter(is_active=True).prefetch_related('courses').order_by('code')
        if has_mappable_rows else []
    )

    return render(request, 'selfpaced/admin/job_detail.html', {
        'job': job,
        'flagged_rows': flagged_rows,
        'created_enrolment_count': created_enrolment_count,
        'pipeline': pipeline,
        'pipeline_pct': pct,
        'programmes_for_mapping': programmes_for_mapping,
        'has_mappable_rows': has_mappable_rows,
    })


@login_required
def job_progress_fragment(request, pk):
    """HTMX endpoint — returns the pipeline progress card fragment only."""
    from selfpaced.engine import PIPELINE_STEPS
    job = get_object_or_404(IngestionJob, pk=pk)
    if job.status != 'processing':
        # Job finished — trigger a full page reload so stats/errors appear.
        return HttpResponse('', headers={'HX-Refresh': 'true'})
    log_by_step = {entry['step']: entry for entry in (job.progress_log or [])}
    steps_done = len(log_by_step)
    pipeline = []
    for i, label in enumerate(PIPELINE_STEPS, start=1):
        entry = log_by_step.get(i)
        pipeline.append({
            'num':        i,
            'label':      label,
            'entry':      entry,
            'is_done':    entry is not None,
            'is_current': not entry and i == steps_done + 1,
            'is_failed':  False,
        })
    pct = int(steps_done / len(PIPELINE_STEPS) * 100)
    return render(request, 'selfpaced/admin/_job_progress.html', {
        'job': job,
        'pipeline': pipeline,
        'pipeline_pct': pct,
    })


@login_required
def preview_poll_fragment(request, pk):
    """HTMX polling fragment — called every ~2s while preview is running.

    - Still previewing  → return the spinner fragment (HTMX swaps it in-place;
                          the trigger fires again in 2 s).
    - Preview done      → HX-Redirect to the full review page.
    - Failed/other      → HX-Redirect to the job detail page.
    """
    from django.urls import reverse
    from django.utils import timezone
    from datetime import timedelta
    job = get_object_or_404(IngestionJob, pk=pk)
    if job.status == 'previewing':
        # Safety net: if still previewing after 3 minutes the background thread
        # died without updating the status — fail it so the UI unsticks.
        if timezone.now() - job.uploaded_at > timedelta(minutes=3):
            job.status = 'failed'
            job.errors = ['Preview timed out — the analysis took too long. Please try uploading again.']
            job.save(update_fields=['status', 'errors'])
            return HttpResponse('', headers={'HX-Redirect': reverse('sp_job_detail', args=[pk])})
        return render(request, 'selfpaced/admin/_preview_status.html', {'job': job})
    if job.status == 'pending_review':
        return HttpResponse('', headers={'HX-Redirect': reverse('sp_job_review', args=[pk])})
    # Failed, cancelled, or anything else — go to job detail
    return HttpResponse('', headers={'HX-Redirect': reverse('sp_job_detail', args=[pk])})


@login_required
@require_POST
def resolve_preview_programme(request, pk):
    """
    Called from the review screen when the admin resolves an unrecognised programme code.

    action=create_new      — creates a Programme record so the detector finds it on re-preview.
    action=map_to_existing — sets programme.ehub_code so the extracted code resolves to an
                             existing programme without creating a duplicate record.

    Re-runs preview_ingestion after the change so counts on the review page update immediately.
    """
    job = get_object_or_404(IngestionJob, pk=pk)
    if job.status != 'pending_review':
        return redirect('sp_job_review', pk=pk)

    action = request.POST.get('action')
    code = (request.POST.get('code') or '').strip().upper()

    if not code:
        messages.error(request, 'Programme code missing.')
        return redirect('sp_job_review', pk=pk)

    if action == 'create_new':
        existing = Programme.objects.filter(code__iexact=code).first()
        if existing:
            messages.info(request, f'Programme "{existing.code}" already exists — no change needed.')
        else:
            Programme.objects.create(code=code, name=code, is_active=True)
            messages.success(
                request,
                f'Programme "{code}" created. Edit the full name under Admin → Programmes.'
            )

    elif action == 'map_to_existing':
        programme_pk = request.POST.get('programme')
        if not programme_pk:
            messages.error(request, 'Select a programme to map to.')
            return redirect('sp_job_review', pk=pk)
        programme = get_object_or_404(Programme, pk=programme_pk)
        if programme.code.upper() == code or (programme.ehub_code or '').upper() == code:
            messages.info(request, f'"{code}" already maps to {programme.code}.')
        else:
            # Guard: ehub_code is UNIQUE — check no other programme already holds this alias
            conflict = Programme.objects.filter(ehub_code__iexact=code).exclude(pk=programme.pk).first()
            if conflict:
                messages.error(
                    request,
                    f'Cannot map "{code}" to {programme.code} — '
                    f'it is already an alias for {conflict.code}. '
                    f'Remove that alias first via the programme admin page.'
                )
                return redirect('sp_job_review', pk=pk)
            programme.ehub_code = code.upper()
            programme.save(update_fields=['ehub_code'])
            messages.success(request, f'"{code}" mapped to {programme.code}.')

    else:
        messages.error(request, 'Unknown action.')
        return redirect('sp_job_review', pk=pk)

    try:
        from selfpaced.engine import preview_ingestion
        preview_ingestion(job.pk)
    except Exception as exc:
        logger.exception('Re-preview for job %d failed after programme resolution', job.pk)
        messages.error(request, f'Programme resolved but preview refresh failed: {exc}')

    return redirect('sp_job_review', pk=pk)


@login_required
def map_preview_pattern(request, pk):
    """
    Map an unrecognised pattern from a pending_review job's preview data.
    Creates a ProgrammeIdentifierRegistry entry then re-runs preview so the
    review page reflects the fix immediately — before ingestion is confirmed.
    """
    job = get_object_or_404(IngestionJob, pk=pk)
    if job.status != 'pending_review':
        return redirect('sp_job_review', pk=pk)

    if request.method == 'POST':
        programme_pk = request.POST.get('programme')
        course_pk = request.POST.get('course') or None
        pattern = (request.POST.get('pattern') or '').strip()

        if programme_pk and pattern:
            programme = get_object_or_404(Programme, pk=programme_pk)
            course = Course.objects.filter(pk=course_pk, programme=programme).first() if course_pk else None

            registry_entry, created = ProgrammeIdentifierRegistry.objects.get_or_create(
                raw_pattern=pattern,
                defaults={
                    'pattern_type': 'ehub_class_name',
                    'programme': programme,
                    'course': course,
                    'created_by': request.user,
                },
            )
            if not created and (registry_entry.programme != programme or registry_entry.course != course):
                registry_entry.programme = programme
                registry_entry.course = course
                registry_entry.save(update_fields=['programme', 'course'])

            # Re-run preview so the review page shows the updated counts
            try:
                from selfpaced.engine import preview_ingestion
                preview_ingestion(job.pk)
            except Exception as exc:
                logger.exception('Re-preview for job %d failed after pattern mapping', job.pk)
                messages.error(request, f'Pattern mapped but preview refresh failed: {exc}')
                return redirect('sp_job_review', pk=pk)

            label = 'updated' if not created else 'mapped'
            messages.success(
                request,
                f'Pattern "{pattern}" {label} → {programme.code}'
                + (f' / {course.full_name}' if course else '')
                + '. Preview updated.'
            )
        else:
            messages.error(request, 'Programme and pattern are required.')

    return redirect('sp_job_review', pk=pk)


@login_required
@require_POST
def recompute_health(request):
    """Start health flag recomputation in a background thread.

    Optional POST params:
      programme_code — limit to one programme
      job_pk         — limit to enrolments touched by one ingestion job
    """
    from selfpaced.engine import get_recompute_status, mark_recompute_starting

    if get_recompute_status()['running']:
        if request.headers.get('HX-Request'):
            return render(request, 'selfpaced/admin/_recompute_status.html', {
                'rc': get_recompute_status(),
            })
        messages.warning(request, 'Recomputation already in progress.')
        return redirect('sp_admin_home')

    programme_code = (request.POST.get('programme_code') or '').strip().upper() or None
    job_pk_raw = request.POST.get('job_pk')
    try:
        job_pk = int(job_pk_raw) if job_pk_raw else None
    except (ValueError, TypeError):
        job_pk = None

    if programme_code:
        scope = f'PROG:{programme_code}'
    elif job_pk:
        scope = f'JOB:{job_pk}'
    else:
        scope = None

    mark_recompute_starting(scope=scope)

    def _run():
        from django.db import close_old_connections
        from selfpaced.engine import recompute_health as _recompute
        close_old_connections()
        try:
            _recompute(programme_code=programme_code, job_pk=job_pk)
        finally:
            close_old_connections()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    if request.headers.get('HX-Request'):
        return render(request, 'selfpaced/admin/_recompute_status.html', {
            'rc': get_recompute_status(),
        })
    return redirect('sp_admin_home')


@login_required
@require_POST
def purge_job_blobs(request):
    """Clear legacy file_content BinaryField blobs from terminal IngestionJobs."""
    from django.db.models import IntegerField, ExpressionWrapper
    from django.db.models.functions import Length
    guard = _require_staff(request)
    if guard:
        return guard
    qs = (
        IngestionJob.objects
        .filter(status__in=('complete', 'failed', 'cancelled'))
        .annotate(_fc_len=ExpressionWrapper(Length('file_content'), output_field=IntegerField()))
        .filter(_fc_len__gt=0)
    )
    jobs = list(qs)
    total_mb = sum(len(bytes(j.file_content)) for j in jobs) / 1024 / 1024
    for job in jobs:
        job.file_content = b''
        job.save(update_fields=['file_content'])
    if jobs:
        messages.success(request, f'Cleared legacy CSV blobs from {len(jobs)} job(s) — freed ~{total_mb:.1f} MB.')
    else:
        messages.info(request, 'No legacy blobs found — database is already clean.')
    return redirect('sp_admin_home')


@login_required
def recompute_health_status(request):
    """HTMX endpoint — returns the recompute widget fragment."""
    from selfpaced.engine import get_recompute_status
    return render(request, 'selfpaced/admin/_recompute_status.html', {
        'rc': get_recompute_status(),
    })


def _run_enrolment_upload(job_pk: int) -> None:
    """Background thread: process a confirmed enrolment CSV upload.

    Optimised for large files: pre-fetches all Learner and Enrolment records
    in two bulk SELECTs, processes rows in Python, then writes with
    bulk_create / bulk_update — regardless of row count only ~6 DB queries.
    """
    from django.db import close_old_connections
    close_old_connections()
    try:
        import csv, io, re, unicodedata as _ud
        from datetime import datetime as _dt
        from selfpaced.models import (
            EnrolmentUploadJob, Enrolment, Learner, Programme, PaymentStatus,
        )

        job = EnrolmentUploadJob.objects.get(pk=job_pk)
        content = bytes(job.file_content)
        text = content.decode('utf-8-sig', errors='replace')
        rows = list(csv.DictReader(io.StringIO(text)))

        col_email = job.column_email
        col_prog  = job.column_programme
        col_date  = job.column_date

        # Filter to monitored countries (blank country passes through)
        from selfpaced.models import MonitoredCountry as _MC
        _active_countries = _MC.active_names_lower()
        if _active_countries:
            rows = [
                r for r in rows
                if not (r.get('Country of residence') or r.get('country') or '').strip()
                or (r.get('Country of residence') or r.get('country') or '').strip().lower()
                   in _active_countries
            ]

        name_to_prog_pk: dict = job.review_data.get('name_to_prog_pk', {})
        if not name_to_prog_pk:
            EnrolmentUploadJob.objects.filter(pk=job_pk).update(
                status='failed',
                errors=['No programme mappings found — go back to the review step and map '
                        'each programme name in the CSV to a programme in LearnSync.'],
            )
            return
        prog_cache: dict[int, Programme] = {
            p.pk: p for p in Programme.objects.filter(pk__in=name_to_prog_pk.values())
        }

        _PAYMENT_MAP = {
            'compliant':    PaymentStatus.COMPLIANT,
            'due soon':     PaymentStatus.DUE_SOON,
            'due_soon':     PaymentStatus.DUE_SOON,
            'grace period': PaymentStatus.GRACE_PERIOD,
            'grace_period': PaymentStatus.GRACE_PERIOD,
            'overdue':      PaymentStatus.OVERDUE,
        }

        # ── helpers ────────────────────────────────────────────────────────
        def _get(row, *keys):
            for k in keys:
                v = (row.get(k) or '').strip()
                if v:
                    return v
            return ''

        def _parse_date(raw):
            if not raw:
                return None
            s = raw.strip()
            s = s.split('T')[0].split(' ')[0] if 'T' in s or (len(s) > 10 and s[10] == ' ') else s
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y',
                        '%Y/%m/%d', '%d %b %Y', '%d %B %Y', '%B %d, %Y', '%b %d, %Y'):
                try:
                    return _dt.strptime(s.strip(), fmt).date()
                except ValueError:
                    continue
            return None

        def _parse_bool(raw):
            return raw.strip().lower() in ('yes', 'true', '1') if raw else False

        _4B = re.compile(r'[\U00010000-\U0010FFFF]')
        _NAME_CATS = frozenset(['Lu','Ll','Lt','Lm','Lo','Mn','Mc','Me','Zs','Pd'])

        def _name(s):
            s = _4B.sub('', s)
            return ''.join(c for c in s if _ud.category(c) in _NAME_CATS or c in "'.''")

        # ── Phase 1: parse rows → clean dicts, no DB ───────────────────────
        parsed = []
        skipped = 0
        for row in rows:
            email     = (row.get(col_email) or '').strip().lower()
            prog_name = (row.get(col_prog)  or '').strip()
            if not email or '@' not in email:
                skipped += 1
                continue
            prog_pk = name_to_prog_pk.get(prog_name)
            if not prog_pk or prog_pk not in prog_cache:
                skipped += 1
                continue
            pay_raw = _get(row, 'Payment status', 'Payment Status', 'payment_status')
            parsed.append({
                'email':           email,
                'prog_pk':         prog_pk,
                'enrolment_date':  _parse_date(_get(row, col_date) if col_date else ''),
                'activation_date': _parse_date(_get(row, 'Activation date', 'Activation Date', 'activation_date')),
                'grad_date':       _parse_date(_get(row, 'Program graduation date', 'Programme graduation date')),
                'is_graduated':    _parse_bool(_get(row, 'Is program graduated', 'Is programme graduated')),
                'first_name':      _name(_get(row, 'First name', 'First Name', 'first_name')),
                'last_name':       _name(_get(row, 'Last name',  'Last Name',  'last_name')),
                'gender':          _name(_get(row, 'Gender', 'gender')),
                'region':          _name(_get(row, 'Regions', 'Region', 'region')),
                'country':         _name(_get(row, 'Country of residence', 'Country', 'country')),
                'phone':           _get(row, 'Phone', 'Phone number', 'Phone Number', 'phone_number',
                                        'Mobile', 'Mobile number', 'Mobile Number', 'Tel', 'Telephone'),
                'ehub_url':        _get(row, 'eHub profile', 'eHub Profile'),
                'lms_url':         _get(row, 'LMS profile',  'LMS Profile'),
                'payment':         _PAYMENT_MAP.get(pay_raw.lower()) if pay_raw else None,
            })

        # Cancel check after Phase 1
        if EnrolmentUploadJob.objects.filter(pk=job_pk, cancel_requested=True).exists():
            EnrolmentUploadJob.objects.filter(pk=job_pk).update(status='cancelled')
            return

        # ── Phase 2: bulk pre-fetch (2 queries) ────────────────────────────
        all_emails   = {p['email']   for p in parsed}
        all_prog_pks = {p['prog_pk'] for p in parsed}

        existing_learners: dict[str, Learner] = {
            l.email: l for l in Learner.objects.filter(email__in=all_emails)
        } if all_emails else {}

        existing_enrolments: dict[tuple, Enrolment] = {
            (e.learner_id, e.programme_id): e
            for e in Enrolment.objects.filter(
                learner_id__in=all_emails,
                programme_id__in=all_prog_pks,
            )
        } if all_emails else {}

        # ── Phase 3: build create / update lists (pure Python) ─────────────
        learners_to_create:    list[Learner]    = []
        learners_to_update:    list[Learner]    = []
        learner_update_fields: set[str]         = set()
        enrolments_to_create:  list[Enrolment]  = []
        enrolments_to_update:  list[Enrolment]  = []
        enrolment_update_fields: set[str]       = set()

        seen_emails: set[str] = set()
        seen_enrolment_keys: set[tuple] = set()   # (email, prog_pk) pairs already queued for create
        created = updated = errors_count = 0
        errors  = []

        for p in parsed:
            email   = p['email']
            prog_pk = p['prog_pk']

            # ── Learner ──
            if email not in seen_emails:
                seen_emails.add(email)
                existing = existing_learners.get(email)
                if existing is None:
                    learners_to_create.append(Learner(
                        email=email,
                        first_name=p['first_name'], last_name=p['last_name'],
                        gender=p['gender'], region=p['region'], country=p['country'],
                        phone_number=p['phone'],
                        ehub_profile_url=p['ehub_url'], lms_profile_url=p['lms_url'],
                        payment_status=p['payment'] or PaymentStatus.UNKNOWN,
                    ))
                else:
                    changed: list[str] = []
                    for attr, val in [
                        ('first_name', p['first_name']), ('last_name', p['last_name']),
                        ('gender', p['gender']),         ('region', p['region']),
                        ('country', p['country']),
                        ('phone_number', p['phone']),
                        ('ehub_profile_url', p['ehub_url']),
                        ('lms_profile_url',  p['lms_url']),
                    ]:
                        if val and getattr(existing, attr) != val:
                            setattr(existing, attr, val)
                            changed.append(attr)
                    if p['payment'] and existing.payment_status != p['payment']:
                        existing.payment_status = p['payment']
                        changed.append('payment_status')
                    if changed:
                        learners_to_update.append(existing)
                        learner_update_fields.update(changed)

            # ── Enrolment ──
            key = (email, prog_pk)
            existing_e = existing_enrolments.get(key)
            if existing_e is None:
                # Guard against duplicate (email, programme) in the CSV — the DB has a
                # unique_together constraint and bulk_create would raise IntegrityError.
                if key in seen_enrolment_keys:
                    skipped += 1
                    continue
                seen_enrolment_keys.add(key)
                enrolments_to_create.append(Enrolment(
                    learner_id=email, programme_id=prog_pk,
                    enrolment_date=p['enrolment_date'],
                    activation_date=p['activation_date'],
                    is_graduated=p['is_graduated'],
                    graduation_date=p['grad_date'],
                ))
                created += 1
            else:
                changed_e: list[str] = []
                for attr, val in [
                    ('enrolment_date',  p['enrolment_date']),
                    ('activation_date', p['activation_date']),
                    ('graduation_date', p['grad_date']),
                ]:
                    if val and getattr(existing_e, attr) != val:
                        setattr(existing_e, attr, val)
                        changed_e.append(attr)
                if p['is_graduated'] and not existing_e.is_graduated:
                    existing_e.is_graduated = True
                    changed_e.append('is_graduated')
                if changed_e:
                    enrolments_to_update.append(existing_e)
                    enrolment_update_fields.update(changed_e)
                    updated += 1

        # Cancel check before writes (last safe stopping point)
        if EnrolmentUploadJob.objects.filter(pk=job_pk, cancel_requested=True).exists():
            EnrolmentUploadJob.objects.filter(pk=job_pk).update(status='cancelled')
            return

        # ── Phase 4: bulk writes ────────────────────────────────────────────
        if learners_to_create:
            Learner.objects.bulk_create(learners_to_create, batch_size=500)
        if learners_to_update and learner_update_fields:
            Learner.objects.bulk_update(
                learners_to_update, list(learner_update_fields), batch_size=500
            )
        if enrolments_to_create:
            Enrolment.objects.bulk_create(enrolments_to_create, batch_size=500)
        if enrolments_to_update and enrolment_update_fields:
            Enrolment.objects.bulk_update(
                enrolments_to_update, list(enrolment_update_fields), batch_size=500
            )

        job.status         = 'complete'
        job.rows_processed = len(rows)
        job.rows_created   = created
        job.rows_updated   = updated
        job.rows_skipped   = skipped
        job.errors         = errors[:50]
        # Do NOT clear file_content here — the re-run button on the detail page
        # re-invokes this function on the same job, so the CSV must be kept.
        # File content is purged only when the job record itself is deleted.
        job.save(update_fields=['status', 'rows_processed', 'rows_created',
                                'rows_updated', 'rows_skipped', 'errors'])

    except Exception as exc:
        from selfpaced.models import EnrolmentUploadJob
        try:
            EnrolmentUploadJob.objects.filter(pk=job_pk).update(
                status='failed', errors=[str(exc)]
            )
        except Exception:
            pass
    finally:
        close_old_connections()


@login_required
def upload_enrolment_csv(request):
    """Upload a programme-enrolment CSV and redirect to the column/mapping review page."""
    from selfpaced.models import EnrolmentUploadJob

    if request.method == 'POST':
        if EnrolmentUploadJob.objects.filter(status='processing').exists():
            messages.warning(
                request,
                'An enrolment upload is currently processing. '
                'Wait for it to finish before uploading another file.',
            )
            return redirect('sp_enrolment_log')
        f = request.FILES.get('file')
        if not f:
            messages.error(request, 'Please select a CSV file.')
            return redirect('sp_upload_enrolment_csv')

        content = f.read()
        try:
            import csv, io
            text = content.decode('utf-8-sig', errors='replace')
            reader = csv.DictReader(io.StringIO(text))
            columns = reader.fieldnames or []
            rows = list(reader)
        except Exception as exc:
            messages.error(request, f'Could not parse CSV: {exc}')
            return redirect('sp_upload_enrolment_csv')

        if not columns:
            messages.error(request, 'CSV appears empty or has no headers.')
            return redirect('sp_upload_enrolment_csv')

        # Auto-detect likely columns (checks exact match first, then substring)
        def _guess(cols, keywords):
            lower_cols = [c.lower() for c in cols]
            for kw in keywords:
                if kw in lower_cols:
                    return cols[lower_cols.index(kw)]
            for kw in keywords:
                for i, lc in enumerate(lower_cols):
                    if kw in lc:
                        return cols[i]
            return ''

        col_email = _guess(columns, ['email'])
        col_prog  = _guess(columns, ['program_name', 'programme_name', 'program', 'programme', 'cohort'])
        col_date  = _guess(columns, ['program enrollment date', 'programme enrollment date',
                                     'program enrolment date', 'programme enrolment date',
                                     'enrollment date', 'enrolment date', 'start date'])

        # Collect unique programme names for mapping
        unique_prog_names = sorted({
            r.get(col_prog, '').strip()
            for r in rows
            if r.get(col_prog, '').strip()
        }) if col_prog else []

        # Unique values for every column — lets the review page rebuild Step 2
        # dynamically when the user picks a different programme column.
        all_col_unique = {
            col: sorted({(r.get(col) or '').strip() for r in rows if (r.get(col) or '').strip()})
            for col in columns
        }

        job = EnrolmentUploadJob.objects.create(
            uploaded_by=request.user,
            file_name=f.name,
            file_content=content,
            column_email=col_email,
            column_programme=col_prog,
            column_date=col_date,
            review_data={
                'columns': columns,
                'row_count': len(rows),
                'unique_prog_names': unique_prog_names,
                'all_col_unique': all_col_unique,
            },
        )
        return redirect('sp_enrolment_review', pk=job.pk)

    return render(request, 'selfpaced/admin/enrolment_upload.html')


@login_required
def review_enrolment_csv(request, pk):
    """Show column selector + programme-name mapping interface; confirm triggers processing."""
    from selfpaced.models import EnrolmentUploadJob, Programme, ProgrammeNameMapping

    job = get_object_or_404(EnrolmentUploadJob, pk=pk, status='pending_review')
    review = job.review_data or {}
    columns = review.get('columns', [])
    programmes = list(Programme.objects.filter(is_active=True).order_by('code'))
    existing_mappings = {m.csv_name: m.programme_id for m in ProgrammeNameMapping.objects.all()}

    if request.method == 'POST':
        # --- 1. Read column selection ---
        col_email = request.POST.get('col_email', '').strip()
        col_prog  = request.POST.get('col_programme', '').strip()
        col_date  = request.POST.get('col_date', '').strip()

        if not col_email or not col_prog:
            messages.error(request, 'Email and Programme columns are required.')
            return redirect('sp_enrolment_review', pk=pk)

        # --- 2. Save/update programme name mappings ---
        # Use unique values for the *selected* column when available (covers the case
        # where the user switched to a different column than was originally auto-detected).
        all_col_unique = review.get('all_col_unique', {})
        unique_names = all_col_unique.get(col_prog) if all_col_unique else None
        if unique_names is None:
            unique_names = review.get('unique_prog_names', [])
        prog_by_pk = {p.pk: p for p in programmes}
        name_to_prog = {}
        for name in unique_names:
            prog_pk_str = request.POST.get(f'mapping_{name}', '').strip()
            if prog_pk_str:
                try:
                    prog_pk = int(prog_pk_str)
                    if prog_pk in prog_by_pk:
                        ProgrammeNameMapping.objects.update_or_create(
                            csv_name=name,
                            defaults={'programme_id': prog_pk, 'created_by': request.user},
                        )
                        name_to_prog[name] = prog_by_pk[prog_pk]
                except (ValueError, TypeError):
                    pass
            elif name in existing_mappings:
                pid = existing_mappings[name]
                if pid in prog_by_pk:
                    name_to_prog[name] = prog_by_pk[pid]

        # --- 3. Persist column mapping + name→pk map on the job, then hand off to thread ---
        review = dict(job.review_data or {})
        review['name_to_prog_pk'] = {name: p.pk for name, p in name_to_prog.items()}

        job.column_email     = col_email
        job.column_programme = col_prog
        job.column_date      = col_date
        job.status           = 'processing'
        job.review_data      = review
        job.save(update_fields=['column_email', 'column_programme', 'column_date',
                                'status', 'review_data'])

        t = threading.Thread(target=_run_enrolment_upload, args=(job.pk,), daemon=True)
        t.start()

        return redirect('sp_enrolment_detail', pk=job.pk)

    # GET — build mapping state for the template
    unique_names = review.get('unique_prog_names', [])
    mapping_rows = []
    for name in unique_names:
        mapped_pk = existing_mappings.get(name)
        mapping_rows.append({'name': name, 'mapped_pk': mapped_pk})

    # Sample rows for the live preview (first 20 rows of the CSV)
    import csv as _csv, io as _io
    from selfpaced.models import Learner as _Learner
    _text  = bytes(job.file_content).decode('utf-8-sig', errors='replace')
    sample = list(_csv.DictReader(_io.StringIO(_text)))[:20]

    # Check which sample emails already exist in the system
    _col_email = job.column_email or ''
    sample_emails = {
        (r.get(_col_email) or '').strip().lower()
        for r in sample
        if (r.get(_col_email) or '').strip()
    }
    existing_emails = list(
        _Learner.objects.filter(email__in=sample_emails).values_list('email', flat=True)
    ) if sample_emails else []

    return render(request, 'selfpaced/admin/enrolment_review.html', {
        'job':            job,
        'review':         review,
        'columns':        columns,
        'programmes':     programmes,
        'mapping_rows':   mapping_rows,
        # Passed as Python objects — serialised by json_script in the template
        'sample_data':          sample,
        'existing_emails_data': existing_emails,
        'prog_code_map_data':   {str(p.pk): p.code for p in programmes},
        'existing_mappings_data': {
            name: str(pk) for name, pk in existing_mappings.items()
        },
        'all_col_unique_data':  review.get('all_col_unique', {}),
        'programmes_list_data': [{'pk': p.pk, 'code': p.code, 'name': p.name} for p in programmes],
    })


@login_required
def enrolment_upload_detail(request, pk):
    """Summary page for an enrolment upload job (auto-reloads while processing)."""
    from selfpaced.models import EnrolmentUploadJob
    job = get_object_or_404(EnrolmentUploadJob, pk=pk)
    total = job.review_data.get('row_count', 0) if job.review_data else 0
    pct = int(job.rows_processed / total * 100) if total and job.rows_processed else 0
    columns = job.review_data.get('columns', []) if job.review_data else []
    return render(request, 'selfpaced/admin/enrolment_detail.html', {
        'job': job,
        'total': total,
        'pct': pct,
        'columns': columns,
    })


@login_required
def enrolment_status_fragment(request, pk):
    """HTMX endpoint — returns the enrolment upload status card fragment."""
    from selfpaced.models import EnrolmentUploadJob
    job = get_object_or_404(EnrolmentUploadJob, pk=pk)
    total = job.review_data.get('row_count', 0) if job.review_data else 0
    pct = int(job.rows_processed / total * 100) if total and job.rows_processed else 0
    return render(request, 'selfpaced/admin/_enrolment_status.html', {
        'job': job, 'total': total, 'pct': pct,
    })


@login_required
@require_POST
def enrolment_reprocess(request, pk):
    """Re-run a completed/failed enrolment upload with a corrected column mapping."""
    from selfpaced.models import EnrolmentUploadJob
    job = get_object_or_404(EnrolmentUploadJob, pk=pk)
    if job.status == 'processing':
        messages.error(request, 'Job is already processing.')
        return redirect('sp_enrolment_detail', pk=pk)

    # Guard: file content was cleared on older jobs before this bug was fixed.
    # If the content is gone the re-run would silently process 0 rows.
    if not job.file_content:
        messages.error(
            request,
            'The original CSV file is no longer stored for this job and cannot be re-run. '
            'Please upload the file again via "Upload Enrolment CSV".',
        )
        return redirect('sp_enrolment_detail', pk=pk)

    col_date = request.POST.get('col_date', '').strip()
    job.column_date = col_date
    job.status = 'processing'
    job.rows_processed = 0
    job.rows_created = 0
    job.rows_updated = 0
    job.rows_skipped = 0
    job.errors = []
    job.save(update_fields=['column_date', 'status', 'rows_processed',
                             'rows_created', 'rows_updated', 'rows_skipped', 'errors'])

    t = threading.Thread(target=_run_enrolment_upload, args=(job.pk,), daemon=True)
    t.start()
    return redirect('sp_enrolment_detail', pk=pk)


@login_required
@require_POST
def delete_enrolment_job(request, pk):
    """Delete a single enrolment upload job record (does not undo processed enrolments)."""
    from selfpaced.models import EnrolmentUploadJob
    job = get_object_or_404(EnrolmentUploadJob, pk=pk)
    if job.status == 'processing':
        messages.error(request, f'Job #{pk} is currently processing — wait for it to finish before deleting.')
        return redirect('sp_enrolment_log')
    file_name = job.file_name
    job.delete()
    messages.success(request, f'Upload "{file_name}" deleted.')
    return redirect('sp_enrolment_log')


@login_required
def enrolment_upload_log(request):
    """List all enrolment upload jobs."""
    from selfpaced.models import EnrolmentUploadJob
    jobs = EnrolmentUploadJob.objects.select_related('uploaded_by').all()
    return render(request, 'selfpaced/admin/enrolment_log.html', {'jobs': jobs})


@login_required
def enrolment_upload_purge(request):
    """Confirm + execute purge of all enrolment upload jobs and their effects."""
    from selfpaced.models import Enrolment, EnrolmentUploadJob, Learner

    job_count    = EnrolmentUploadJob.objects.count()
    date_count   = Enrolment.objects.filter(enrolment_date__isnull=False).count()
    # Learners created only by the upload have no first_seen_date (main ingestion sets it)
    orphan_count = Learner.objects.filter(first_seen_date__isnull=True).count()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action != 'confirm':
            return redirect('sp_enrolment_log')

        # 1. NULL out enrolment_date on all enrolments (exclusively set by upload)
        Enrolment.objects.update(enrolment_date=None)

        # 2. Optionally delete orphan learners (no first_seen_date = never in main ingestion)
        delete_orphans = request.POST.get('delete_orphans') == '1'
        orphans_deleted = 0
        if delete_orphans:
            orphans_deleted, _ = Learner.objects.filter(first_seen_date__isnull=True).delete()

        # 3. Delete all upload job records
        EnrolmentUploadJob.objects.all().delete()

        messages.success(
            request,
            f'Purged {job_count} upload job(s). Cleared enrolment dates on {date_count} record(s).'
            + (f' Deleted {orphans_deleted} learner(s) not in main ingestion.' if orphans_deleted else '')
        )
        return redirect('sp_enrolment_log')

    return render(request, 'selfpaced/admin/enrolment_purge.html', {
        'job_count':    job_count,
        'date_count':   date_count,
        'orphan_count': orphan_count,
    })


@login_required
def country_settings(request):
    """Checklist of African countries the system monitors. Saves which are active."""
    from selfpaced.models import MonitoredCountry

    if request.method == 'POST':
        all_countries = list(MonitoredCountry.objects.all())
        active_names = set(request.POST.getlist('countries'))
        to_update = []
        for c in all_countries:
            want = c.name in active_names
            if c.is_active != want:
                c.is_active = want
                to_update.append(c)
        if to_update:
            MonitoredCountry.objects.bulk_update(to_update, ['is_active'])
        messages.success(request, f'{len([c for c in all_countries if c.name in active_names])} countries marked as monitored.')
        return redirect('sp_country_settings')

    from selfpaced.models import Learner
    from django.db.models import Count

    countries = list(MonitoredCountry.objects.all())
    active_count = sum(1 for c in countries if c.is_active)

    country_counts = list(
        Learner.objects
        .exclude(country='')
        .values('country')
        .annotate(n=Count('email'))
        .order_by('-n')
    )
    no_country_count = Learner.objects.filter(country='').count()

    return render(request, 'selfpaced/admin/country_settings.html', {
        'countries': countries,
        'active_count': active_count,
        'country_counts': country_counts,
        'no_country_count': no_country_count,
    })


@login_required
@require_POST
def purge_unmonitored_learners(request):
    """Remove learners whose country is not in the monitored list."""
    from selfpaced.models import Learner, MonitoredCountry

    active_lower = MonitoredCountry.active_names_lower()
    if not active_lower:
        messages.warning(request, 'No countries are monitored — purge aborted to avoid removing all learners.')
        return redirect('sp_country_settings')

    delete_emails = [
        l.email for l in Learner.objects.exclude(country='')
        if l.country.lower() not in active_lower
    ]
    count = len(delete_emails)
    if count:
        Learner.objects.filter(email__in=delete_emails).delete()
    messages.success(request, f'{count} learner{"s" if count != 1 else ""} from unmonitored countries removed.')
    return redirect('sp_country_settings')


@login_required
def pattern_registry(request):
    """List and manage all ProgrammeIdentifierRegistry entries."""
    from selfpaced.models import ProgrammeIdentifierRegistry as PIR

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            entry_pk = request.POST.get('entry_pk')
            entry = get_object_or_404(PIR, pk=entry_pk)
            pattern = entry.raw_pattern
            entry.delete()
            messages.success(request, f'Pattern "{pattern}" removed from registry.')
            return redirect('sp_pattern_registry')

        elif action == 'add':
            pattern = (request.POST.get('pattern') or '').strip()
            programme_pk = request.POST.get('programme')
            course_pk = request.POST.get('course') or None
            pattern_type = request.POST.get('pattern_type', 'ehub_class_name')

            if not pattern or not programme_pk:
                messages.error(request, 'Pattern and programme are required.')
            else:
                programme = get_object_or_404(Programme, pk=programme_pk)
                course = Course.objects.filter(pk=course_pk, programme=programme).first() if course_pk else None
                _, created = PIR.objects.get_or_create(
                    raw_pattern=pattern,
                    defaults={
                        'pattern_type': pattern_type,
                        'programme': programme,
                        'course': course,
                        'created_by': request.user,
                    },
                )
                if created:
                    messages.success(request, f'Pattern "{pattern}" added → {programme.code}.')
                else:
                    messages.warning(request, f'Pattern "{pattern}" already exists in registry.')
            return redirect('sp_pattern_registry')

    entries = (
        PIR.objects
        .select_related('programme', 'course')
        .order_by('programme__code', 'raw_pattern')
    )
    programmes = Programme.objects.filter(is_active=True).prefetch_related('courses').order_by('code')

    return render(request, 'selfpaced/admin/pattern_registry.html', {
        'entries': entries,
        'programmes': programmes,
    })


@login_required
@require_POST
def edit_preview_course(request, pk):
    """
    Edit a new course's name in the review_data JSON before confirming ingestion.
    Also supports matching a new course to an existing one (map_to_course_pk).

    POST fields:
      prog_code      — programme code (identifies which breakdown entry)
      seq            — course sequence number (integer)
      new_name       — updated name (blank = no change)
      map_to_course_pk — existing Course pk to map this pending course to (optional)
    """
    job = get_object_or_404(IngestionJob, pk=pk, status='pending_review')
    review = job.review_data or {}

    prog_code = (request.POST.get('prog_code') or '').strip().upper()
    try:
        seq = int(request.POST.get('seq', ''))
    except (ValueError, TypeError):
        return redirect('sp_job_review', pk=pk)

    new_name = (request.POST.get('new_name') or '').strip()
    map_to_pk_raw = (request.POST.get('map_to_course_pk') or '').strip()

    # Locate the course entry in programme_breakdown
    breakdown = review.get('programme_breakdown', [])
    for prog_entry in breakdown:
        if prog_entry.get('code', '').upper() == prog_code:
            for course_entry in prog_entry.get('courses', []):
                if course_entry.get('seq') == seq and course_entry.get('is_new'):
                    if new_name:
                        course_entry['name'] = new_name
                    if map_to_pk_raw:
                        try:
                            target_pk = int(map_to_pk_raw)
                            target = Course.objects.filter(pk=target_pk).first()
                            if target:
                                course_entry['is_new'] = False
                                course_entry['course_pk'] = target.pk
                                course_entry['name'] = target.full_name
                                course_entry['mapped_from_name'] = new_name or course_entry.get('name', '')
                                # Adjust new_course_count for this programme
                                prog_entry['new_course_count'] = max(
                                    0, prog_entry.get('new_course_count', 1) - 1
                                )
                        except (ValueError, TypeError):
                            pass
                    break
            break

    # Store course_overrides for _execute to use: {prog_code|seq -> target_course_pk}
    if map_to_pk_raw:
        overrides = review.setdefault('course_overrides', {})
        overrides[f'{prog_code}|{seq}'] = int(map_to_pk_raw)

    job.review_data = review
    job.save(update_fields=['review_data'])

    return redirect('sp_job_review', pk=pk)


# ===========================================================================
# Pod Import (Google Form CSV)
# ===========================================================================

def _run_pod_import(job_pk: int) -> None:
    """Background thread: process a confirmed pod-selection CSV upload.

    Optimised: all Learner, PodAssignment, Pod, and Enrolment records are
    pre-fetched in bulk; the row loop does no DB I/O; writes use bulk_create /
    bulk_update — total queries is ~8 regardless of file size.
    """
    from django.db import close_old_connections
    close_old_connections()
    try:
        import calendar, csv, io
        from datetime import datetime as _dt, date as _date
        from selfpaced.models import (
            Enrolment, Learner, Pod, PodAssignment, PodImportJob,
            PodAssignmentMethod, PodStatus,
        )

        job = PodImportJob.objects.get(pk=job_pk)
        text = bytes(job.file_content).decode('utf-8-sig', errors='replace')
        rows = list(csv.DictReader(io.StringIO(text)))

        col_email        = job.column_email
        col_prog         = job.column_programme
        col_target       = job.column_target_month
        col_enrol        = job.column_enrol_month
        name_to_prog_pk  = job.review_data.get('name_to_prog_pk', {})

        from selfpaced.models import Programme as _Prog
        prog_cache = {p.pk: p for p in _Prog.objects.filter(pk__in=name_to_prog_pk.values())}

        # ── helpers ────────────────────────────────────────────────────────
        def _parse_month(raw):
            if not raw:
                return None
            s = raw.strip()
            today = _date.today()
            for fmt in ('%B %Y', '%b %Y', '%Y-%m', '%m/%Y', '%m-%Y', '%B, %Y', '%b, %Y'):
                try:
                    d = _dt.strptime(s, fmt)
                    return _date(d.year, d.month, 1)
                except ValueError:
                    continue
            for fmt in ('%B', '%b'):
                try:
                    d = _dt.strptime(s, fmt)
                    year = today.year
                    if d.month < today.month:
                        year += 1
                    return _date(year, d.month, 1)
                except ValueError:
                    continue
            return None

        def _month_end(d):
            return _date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])

        # ── Phase 1: parse all rows (no DB) ────────────────────────────────
        parsed     = []
        skipped    = 0
        skip_reasons: dict = {}

        def _skip(reason, example=''):
            nonlocal skipped
            skipped += 1
            skip_reasons.setdefault(reason, [])
            if example and len(skip_reasons[reason]) < 3:
                skip_reasons[reason].append(example)

        for row in rows:
            email     = (row.get(col_email) or '').strip().lower()
            prog_name = (row.get(col_prog)  or '').strip()
            if not email or '@' not in email:
                _skip('Invalid or missing email', email or '(blank)')
                continue
            prog_pk = name_to_prog_pk.get(prog_name)
            if not prog_pk or prog_pk not in prog_cache:
                _skip('Programme not mapped', prog_name or '(blank)')
                continue
            target_start = _parse_month(row.get(col_target, ''))
            if not target_start:
                _skip('Could not parse target month', row.get(col_target, '') or '(blank)')
                continue
            parsed.append({
                'email':        email,
                'prog_pk':      prog_pk,
                'target_date':  _month_end(target_start),
                'enrol_start':  _parse_month(row.get(col_enrol, '')) if col_enrol else None,
            })

        # Cancel check after Phase 1
        if PodImportJob.objects.filter(pk=job_pk, cancel_requested=True).exists():
            PodImportJob.objects.filter(pk=job_pk).update(status='cancelled')
            return

        # ── Phase 2: bulk pre-fetch ────────────────────────────────────────
        all_emails   = {p['email']   for p in parsed}
        all_prog_pks = {p['prog_pk'] for p in parsed}
        all_target_dates = {(p['prog_pk'], p['target_date']) for p in parsed}

        # Learners (read-only lookup — skip unknowns)
        known_learners: set[str] = set(
            Learner.objects.filter(email__in=all_emails).values_list('email', flat=True)
        ) if all_emails else set()

        # Current pod assignments: (email, prog_pk) → PodAssignment
        current_assignments: dict[tuple, PodAssignment] = {
            (a.learner_id, a.programme_id): a
            for a in PodAssignment.objects.filter(
                learner_id__in=all_emails,
                programme_id__in=all_prog_pks,
                is_current=True,
            ).select_related('pod')
        } if all_emails else {}

        # Existing pods: (prog_pk, target_date) → Pod
        pod_cache: dict[tuple, Pod] = {
            (pod.programme_id, pod.target_month): pod
            for pod in Pod.objects.filter(
                programme_id__in=all_prog_pks,
                target_month__in={td for _, td in all_target_dates},
            )
        } if all_target_dates else {}

        # Create missing pods (usually very few)
        missing_pods = [
            Pod(
                programme_id=prog_pk,
                target_month=td,
                name=f'{prog_cache[prog_pk].code} {td:%b %Y}',
                status=PodStatus.ACTIVE,
            )
            for prog_pk, td in all_target_dates
            if (prog_pk, td) not in pod_cache and prog_pk in prog_cache
        ]
        if missing_pods:
            Pod.objects.bulk_create(missing_pods, ignore_conflicts=True, batch_size=200)
            # Re-fetch to get PKs of newly created + any that already existed
            pod_cache = {
                (pod.programme_id, pod.target_month): pod
                for pod in Pod.objects.filter(
                    programme_id__in=all_prog_pks,
                    target_month__in={td for _, td in all_target_dates},
                )
            }

        # Enrolments for date backfill: (email, prog_pk) → Enrolment
        enrolments_cache: dict[tuple, Enrolment] = {
            (e.learner_id, e.programme_id): e
            for e in Enrolment.objects.filter(
                learner_id__in=all_emails,
                programme_id__in=all_prog_pks,
            )
        } if all_emails else {}

        # ── Phase 3: build write lists (pure Python) ───────────────────────
        assignments_to_close:  list[PodAssignment] = []
        assignments_to_create: list[PodAssignment] = []
        enrolments_to_update:  list[Enrolment]     = []
        today = _date.today()
        created = updated = 0
        errors  = []

        for p in parsed:
            email   = p['email']
            prog_pk = p['prog_pk']

            if email not in known_learners:
                _skip('Learner not found in system', email)
                continue

            pod_key = (prog_pk, p['target_date'])
            pod = pod_cache.get(pod_key)
            if pod is None:
                errors.append(f'{email}: pod ({prog_pk}, {p["target_date"]}) missing after create')
                continue

            existing = current_assignments.get((email, prog_pk))
            if existing:
                if existing.pod_id == pod.pk:
                    updated += 1  # already correct
                else:
                    existing.is_current        = False
                    existing.pod_switch_date   = today
                    existing.pod_switch_reason = 'Pod import — learner changed target month'
                    existing.previous_pod_id   = existing.pod_id
                    assignments_to_close.append(existing)
                    assignments_to_create.append(PodAssignment(
                        learner_id=email, programme_id=prog_pk, pod=pod,
                        method=PodAssignmentMethod.SELF_SELECTED, is_current=True,
                    ))
                    updated += 1
            else:
                assignments_to_create.append(PodAssignment(
                    learner_id=email, programme_id=prog_pk, pod=pod,
                    method=PodAssignmentMethod.SELF_SELECTED, is_current=True,
                ))
                created += 1

            # Back-fill enrolment_date if missing
            if p['enrol_start']:
                enrolment = enrolments_cache.get((email, prog_pk))
                if enrolment and not enrolment.enrolment_date:
                    enrolment.enrolment_date = p['enrol_start']
                    enrolments_to_update.append(enrolment)

        # Cancel check before writes
        if PodImportJob.objects.filter(pk=job_pk, cancel_requested=True).exists():
            PodImportJob.objects.filter(pk=job_pk).update(status='cancelled')
            return

        # ── Phase 4: bulk writes ────────────────────────────────────────────
        if assignments_to_close:
            PodAssignment.objects.bulk_update(
                assignments_to_close,
                ['is_current', 'pod_switch_date', 'pod_switch_reason', 'previous_pod_id'],
                batch_size=500,
            )
        if assignments_to_create:
            PodAssignment.objects.bulk_create(assignments_to_create, batch_size=500)
        if enrolments_to_update:
            Enrolment.objects.bulk_update(
                enrolments_to_update, ['enrolment_date'], batch_size=500
            )

        # Append skip-reason summary
        for reason, examples in skip_reasons.items():
            eg = ', '.join(f'"{e}"' for e in examples)
            errors.append(f'{reason} — {len(examples)} example(s): {eg}')

        job.rows_processed = len(rows)
        job.rows_created   = created
        job.rows_updated   = updated
        job.rows_skipped   = skipped
        job.errors         = errors
        job.status         = 'complete' if not errors or created + updated > 0 else 'failed'
        _completed = job.status == 'complete'
        if _completed:
            job.file_content = b''
        _fields = ['rows_processed', 'rows_created', 'rows_updated', 'rows_skipped', 'errors', 'status']
        if _completed:
            _fields.append('file_content')
        job.save(update_fields=_fields)

    except Exception as exc:
        try:
            from selfpaced.models import PodImportJob as _J
            _J.objects.filter(pk=job_pk).update(status='failed', errors=[str(exc)])
        except Exception:
            pass
    finally:
        close_old_connections()


@login_required
def upload_pod_csv(request):
    """Upload a Google Form pod-selection CSV."""
    from selfpaced.models import PodImportJob, Programme

    if request.method == 'POST':
        if PodImportJob.objects.filter(status__in=['pending_review', 'processing']).exists():
            messages.warning(
                request,
                'A pod import is already pending review or processing. '
                'Wait for it to finish before uploading another file.',
            )
            return redirect('sp_pod_import_log')
        f = request.FILES.get('file')
        if not f:
            messages.error(request, 'Please select a CSV file.')
            return redirect('sp_upload_pod_csv')

        import csv, io
        content = f.read()
        try:
            text    = content.decode('utf-8-sig', errors='replace')
            reader  = csv.DictReader(io.StringIO(text))
            columns = reader.fieldnames or []
            rows    = list(reader)
        except Exception as exc:
            messages.error(request, f'Could not parse CSV: {exc}')
            return redirect('sp_upload_pod_csv')

        if not columns:
            messages.error(request, 'CSV appears empty or has no headers.')
            return redirect('sp_upload_pod_csv')

        def _guess(cols, keywords):
            lc = [c.lower() for c in cols]
            for kw in keywords:
                if kw in lc: return cols[lc.index(kw)]
            for kw in keywords:
                for i, c in enumerate(lc):
                    if kw in c: return cols[i]
            return ''

        col_email   = _guess(columns, ['email', 'alx associated email', 'associated email'])
        col_prog    = _guess(columns, ['program', 'programme', 'enrolled in', 'which program'])
        col_target  = _guess(columns, ['finish', 'expecting to finish', 'target', 'completion month'])
        col_enrol   = _guess(columns, ['what month did you enrol', 'enrolment month', 'month did you enrol'])

        unique_prog_names = sorted({
            (r.get(col_prog) or '').strip()
            for r in rows if col_prog and (r.get(col_prog) or '').strip()
        })

        job = PodImportJob.objects.create(
            uploaded_by=request.user,
            file_name=f.name,
            file_content=content,
            column_email=col_email,
            column_programme=col_prog,
            column_target_month=col_target,
            column_enrol_month=col_enrol,
            review_data={
                'columns': list(columns),
                'row_count': len(rows),
                'unique_prog_names': unique_prog_names,
            },
        )
        return redirect('sp_pod_import_review', pk=job.pk)

    return render(request, 'selfpaced/admin/pod_import_upload.html')


@login_required
def review_pod_csv(request, pk):
    """Review column mapping + programme name mapping before processing."""
    from selfpaced.models import PodImportJob, Programme, ProgrammeNameMapping

    job      = get_object_or_404(PodImportJob, pk=pk, status='pending_review')
    review   = job.review_data or {}
    columns  = review.get('columns', [])
    programmes = list(Programme.objects.filter(is_active=True).order_by('code'))
    existing_mappings = {m.csv_name: m.programme_id for m in ProgrammeNameMapping.objects.all()}

    if request.method == 'POST':
        col_email  = request.POST.get('col_email', '').strip()
        col_prog   = request.POST.get('col_programme', '').strip()
        col_target = request.POST.get('col_target', '').strip()
        col_enrol  = request.POST.get('col_enrol', '').strip()

        if not col_email or not col_prog or not col_target:
            messages.error(request, 'Email, Programme, and Target Month columns are required.')
            return redirect('sp_pod_import_review', pk=pk)

        # Save/update programme name mappings
        prog_by_pk    = {p.pk: p for p in programmes}
        name_to_prog  = {}
        for name in review.get('unique_prog_names', []):
            pk_str = request.POST.get(f'mapping_{name}', '').strip()
            if pk_str:
                try:
                    ppk = int(pk_str)
                    if ppk in prog_by_pk:
                        ProgrammeNameMapping.objects.update_or_create(
                            csv_name=name,
                            defaults={'programme_id': ppk, 'created_by': request.user},
                        )
                        name_to_prog[name] = ppk
                except (ValueError, TypeError):
                    pass
            elif name in existing_mappings and existing_mappings[name] in prog_by_pk:
                name_to_prog[name] = existing_mappings[name]

        new_review = dict(review)
        new_review['name_to_prog_pk'] = name_to_prog

        job.column_email        = col_email
        job.column_programme    = col_prog
        job.column_target_month = col_target
        job.column_enrol_month  = col_enrol
        job.status              = 'processing'
        job.review_data         = new_review
        job.save(update_fields=['column_email', 'column_programme', 'column_target_month',
                                 'column_enrol_month', 'status', 'review_data'])

        t = threading.Thread(target=_run_pod_import, args=(job.pk,), daemon=True)
        t.start()
        return redirect('sp_pod_import_detail', pk=job.pk)

    unique_names  = review.get('unique_prog_names', [])
    mapping_rows  = [{'name': n, 'mapped_pk': existing_mappings.get(n)} for n in unique_names]

    return render(request, 'selfpaced/admin/pod_import_review.html', {
        'job':          job,
        'review':       review,
        'columns':      columns,
        'programmes':   programmes,
        'mapping_rows': mapping_rows,
    })


@login_required
def pod_import_detail(request, pk):
    """Progress + result page for a pod import job."""
    from selfpaced.models import PodImportJob
    job   = get_object_or_404(PodImportJob, pk=pk)
    total = (job.review_data or {}).get('row_count', 0)
    pct   = int(job.rows_processed / total * 100) if total and job.rows_processed else 0
    return render(request, 'selfpaced/admin/pod_import_detail.html', {
        'job': job, 'total': total, 'pct': pct,
    })


@login_required
def pod_import_status_fragment(request, pk):
    """HTMX endpoint — returns the pod import status card fragment."""
    from selfpaced.models import PodImportJob
    job = get_object_or_404(PodImportJob, pk=pk)
    total = (job.review_data or {}).get('row_count', 0)
    pct = int(job.rows_processed / total * 100) if total and job.rows_processed else 0
    return render(request, 'selfpaced/admin/_pod_import_status.html', {
        'job': job, 'total': total, 'pct': pct,
    })


@login_required
@require_POST
def pod_import_rerun(request, pk):
    """Re-run a completed/failed pod import job."""
    from selfpaced.models import PodImportJob
    job = get_object_or_404(PodImportJob, pk=pk)
    if job.status == 'processing':
        messages.error(request, 'Job is already processing.')
        return redirect('sp_pod_import_detail', pk=pk)
    job.status = 'processing'
    job.rows_processed = 0
    job.rows_created = 0
    job.rows_updated = 0
    job.rows_skipped = 0
    job.errors = []
    job.save(update_fields=['status', 'rows_processed', 'rows_created',
                             'rows_updated', 'rows_skipped', 'errors'])
    t = threading.Thread(target=_run_pod_import, args=(job.pk,), daemon=True)
    t.start()
    return redirect('sp_pod_import_detail', pk=pk)


@login_required
def pod_import_log(request):
    """List all pod import jobs."""
    from selfpaced.models import PodImportJob
    jobs = PodImportJob.objects.select_related('uploaded_by').all()
    return render(request, 'selfpaced/admin/pod_import_log.html', {'jobs': jobs})


# ── User management ──────────────────────────────────────────────────────────

def _require_staff(request):
    """Return an HttpResponse redirect if the requesting user is not staff."""
    if not request.user.is_staff:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Staff access required.')
    return None


@login_required
def user_list(request):
    """List all platform users."""
    guard = _require_staff(request)
    if guard:
        return guard
    from django.contrib.auth import get_user_model
    User = get_user_model()
    users = User.objects.order_by('-is_superuser', '-is_staff', 'username')
    return render(request, 'selfpaced/admin/user_list.html', {'users': users})


@login_required
def user_create(request):
    """Create a new platform user."""
    guard = _require_staff(request)
    if guard:
        return guard
    from django.contrib.auth import get_user_model
    User = get_user_model()

    errors = {}
    data = {}

    if request.method == 'POST':
        data = request.POST
        username   = data.get('username', '').strip()
        email      = data.get('email', '').strip()
        first_name = data.get('first_name', '').strip()
        last_name  = data.get('last_name', '').strip()
        password   = data.get('password', '')
        password2  = data.get('password2', '')
        is_staff   = data.get('is_staff') == 'on'
        is_super   = data.get('is_superuser') == 'on'

        if not username:
            errors['username'] = 'Username is required.'
        elif User.objects.filter(username=username).exists():
            errors['username'] = 'A user with this username already exists.'
        if not password:
            errors['password'] = 'Password is required.'
        elif len(password) < 8:
            errors['password'] = 'Password must be at least 8 characters.'
        elif password != password2:
            errors['password2'] = 'Passwords do not match.'
        # Only a superuser may create another superuser
        if is_super and not request.user.is_superuser:
            errors['is_superuser'] = 'Only superusers can grant superuser status.'

        if not errors:
            user = User.objects.create_user(
                username=username, email=email,
                first_name=first_name, last_name=last_name,
                password=password,
                is_staff=is_staff, is_superuser=is_super,
            )
            messages.success(request, f'User "{user.username}" created successfully.')
            return redirect('sp_user_list')

    return render(request, 'selfpaced/admin/user_form.html', {
        'action': 'create',
        'data': data,
        'errors': errors,
    })


@login_required
def user_edit(request, pk):
    """Edit an existing platform user."""
    guard = _require_staff(request)
    if guard:
        return guard
    from django.contrib.auth import get_user_model
    User = get_user_model()
    target = get_object_or_404(User, pk=pk)

    errors = {}
    data = {
        'username': target.username,
        'email': target.email,
        'first_name': target.first_name,
        'last_name': target.last_name,
        'is_staff': target.is_staff,
        'is_superuser': target.is_superuser,
        'is_active': target.is_active,
    }

    if request.method == 'POST':
        data = request.POST
        username   = data.get('username', '').strip()
        email      = data.get('email', '').strip()
        first_name = data.get('first_name', '').strip()
        last_name  = data.get('last_name', '').strip()
        password   = data.get('password', '').strip()
        password2  = data.get('password2', '').strip()
        is_staff   = data.get('is_staff') == 'on'
        is_super   = data.get('is_superuser') == 'on'
        is_active  = data.get('is_active') == 'on'

        if not username:
            errors['username'] = 'Username is required.'
        elif User.objects.filter(username=username).exclude(pk=pk).exists():
            errors['username'] = 'A user with this username already exists.'
        if password:
            if len(password) < 8:
                errors['password'] = 'Password must be at least 8 characters.'
            elif password != password2:
                errors['password2'] = 'Passwords do not match.'
        # Only a superuser may grant superuser status
        if is_super and not request.user.is_superuser:
            errors['is_superuser'] = 'Only superusers can grant superuser status.'
        # Prevent self-deactivation
        if target.pk == request.user.pk and not is_active:
            errors['is_active'] = 'You cannot deactivate your own account.'

        if not errors:
            target.username   = username
            target.email      = email
            target.first_name = first_name
            target.last_name  = last_name
            target.is_staff   = is_staff
            target.is_active  = is_active
            if request.user.is_superuser:
                target.is_superuser = is_super
            if password:
                target.set_password(password)
            target.save()
            messages.success(request, f'User "{target.username}" updated successfully.')
            return redirect('sp_user_list')

    return render(request, 'selfpaced/admin/user_form.html', {
        'action': 'edit',
        'target': target,
        'data': data,
        'errors': errors,
    })


@login_required
@require_POST
def user_toggle_active(request, pk):
    """Activate or deactivate a user account."""
    guard = _require_staff(request)
    if guard:
        return guard
    from django.contrib.auth import get_user_model
    User = get_user_model()
    target = get_object_or_404(User, pk=pk)
    if target.pk == request.user.pk:
        messages.error(request, 'You cannot deactivate your own account.')
    else:
        target.is_active = not target.is_active
        target.save(update_fields=['is_active'])
        state = 'activated' if target.is_active else 'deactivated'
        messages.success(request, f'User "{target.username}" {state}.')
    return redirect('sp_user_list')
