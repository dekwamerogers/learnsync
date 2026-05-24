from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from selfpaced.exports import export_interventions_csv
from selfpaced.models import Enrolment, Intervention, InterventionOutcome, InterventionType, Learner


@login_required
def intervention_list(request):
    tab = request.GET.get('tab', 'follow_ups')
    today = date.today()

    follow_ups_qs = (
        Intervention.objects
        .filter(follow_up_required=True, follow_up_date__lte=today)
        .select_related('learner', 'enrolment__programme', 'logged_by')
        .order_by('follow_up_date')
    )

    all_qs = (
        Intervention.objects
        .select_related('learner', 'enrolment__programme', 'logged_by')
        .order_by('-intervention_date', '-logged_date')
    )

    q = request.GET.get('q', '').strip()
    if q:
        all_qs = all_qs.filter(
            Q(learner__email__icontains=q)
            | Q(learner__first_name__icontains=q)
            | Q(learner__last_name__icontains=q)
            | Q(reason__icontains=q)
        )

    if request.GET.get('export') == 'csv':
        return export_interventions_csv(all_qs)

    paginator = Paginator(all_qs, 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    types = InterventionType.choices
    outcomes = InterventionOutcome.choices

    return render(request, 'selfpaced/intervention_list.html', {
        'tab': tab,
        'today': today,
        'follow_ups': follow_ups_qs,
        'follow_up_count': follow_ups_qs.count(),
        'page_obj': page_obj,
        'total': paginator.count,
        'q': q,
        'types': types,
        'outcomes': outcomes,
    })


@login_required
@require_POST
def log_intervention(request):
    """HTMX-free JSON endpoint for logging interventions from the modal form."""
    learner_email = request.POST.get('learner_email', '').strip()
    intervention_date = request.POST.get('intervention_date') or str(date.today())
    iv_type = request.POST.get('type', '')
    outcome = request.POST.get('outcome', '')
    reason = request.POST.get('reason', '')
    notes = request.POST.get('notes', '')
    follow_up = request.POST.get('follow_up_required') == '1'
    follow_up_date = request.POST.get('follow_up_date') or None
    enrolment_id = request.POST.get('enrolment_id') or None

    if not learner_email or not iv_type or not outcome:
        return JsonResponse({'ok': False, 'error': 'Missing required fields.'}, status=400)

    learner = get_object_or_404(Learner, pk=learner_email)
    enrolment = None
    if enrolment_id:
        try:
            enrolment = Enrolment.objects.get(pk=enrolment_id, learner=learner)
        except Enrolment.DoesNotExist:
            pass

    Intervention.objects.create(
        learner=learner,
        enrolment=enrolment,
        intervention_date=intervention_date,
        logged_by=request.user,
        type=iv_type,
        outcome=outcome,
        reason=reason,
        notes=notes,
        follow_up_required=follow_up,
        follow_up_date=follow_up_date if follow_up else None,
    )
    return JsonResponse({'ok': True})


@login_required
@require_POST
def bulk_log_intervention(request):
    """Log the same intervention for multiple learners at once. Returns JSON."""
    import uuid
    from selfpaced.filters import LearnerFilter
    from selfpaced.querysets import real_learners_qs

    intervention_date = request.POST.get('intervention_date') or str(date.today())
    iv_type = request.POST.get('type', '')
    outcome = request.POST.get('outcome', '') or 'not_applicable'
    reason = request.POST.get('reason', '')
    notes = request.POST.get('notes', '')
    follow_up = request.POST.get('follow_up_required') == '1'
    follow_up_date = request.POST.get('follow_up_date') or None

    if not iv_type:
        return JsonResponse({'ok': False, 'error': 'Missing required fields.'}, status=400)

    if request.POST.get('select_all') == '1':
        # Re-apply the learner list filter from the GET query string so the
        # intervention is logged for every learner matching the current filter,
        # not just those visible on the current page.
        f = LearnerFilter(request.GET, queryset=real_learners_qs())
        learners = f.qs.distinct()
    else:
        emails = request.POST.getlist('learner_emails')
        if not emails:
            return JsonResponse({'ok': False, 'error': 'No learners selected.'}, status=400)
        learners = Learner.objects.filter(email__in=emails)

    batch_id = str(uuid.uuid4())
    created = 0
    for learner in learners:
        Intervention.objects.create(
            learner=learner,
            enrolment=None,
            intervention_date=intervention_date,
            logged_by=request.user,
            type=iv_type,
            outcome=outcome,
            reason=reason,
            notes=notes,
            follow_up_required=follow_up,
            follow_up_date=follow_up_date if follow_up else None,
            initiative_id=batch_id,
        )
        created += 1

    return JsonResponse({'ok': True, 'created': created, 'batch_id': batch_id})
