import csv
from datetime import date

from django.http import HttpResponse

_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _sf(value) -> str:
    """Sanitize a value against CSV formula injection (Excel/Sheets DDE attacks)."""
    s = str(value) if value is not None else ''
    if s and s[0] in _FORMULA_PREFIXES:
        return "'" + s
    return s


def _csv_response(filename):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def export_learners_csv(queryset):
    today = date.today()
    response = _csv_response(f'learners_{today:%Y%m%d}.csv')
    writer = csv.writer(response)
    writer.writerow([
        'Email', 'First Name', 'Last Name', 'Phone', 'Country', 'Region',
        'Overall Health', 'Programme Health',
        'Payment Status', 'Programmes', 'First Seen', 'Last Updated',
    ])
    for learner in queryset.prefetch_related('enrolments__programme'):
        enrolments = list(learner.enrolments.all())
        programmes = ', '.join(e.programme.code for e in enrolments)
        # Per-programme health: "AICE:active, WALX:graduated"
        prog_health = ', '.join(
            f"{e.programme.code}:{e.health_status}"
            for e in enrolments
        )
        writer.writerow([
            _sf(learner.email),
            _sf(learner.first_name),
            _sf(learner.last_name),
            _sf(learner.phone_number),
            _sf(learner.country),
            _sf(learner.region),
            learner.overall_health_status,
            _sf(prog_health),
            learner.payment_status,
            _sf(programmes),
            learner.first_seen_date or '',
            learner.last_updated_date.date() if learner.last_updated_date else '',
        ])
    return response


def export_enrolments_csv(queryset):
    today = date.today()
    response = _csv_response(f'enrolments_{today:%Y%m%d}.csv')
    writer = csv.writer(response)
    writer.writerow([
        'Email', 'Name', 'Programme', 'Health', 'Active Flags',
        'Current Course', 'Enrolment Date', 'First Activity',
        'Graduated', 'Payment Status', 'Last Updated',
    ])
    for enrolment in queryset.select_related('learner', 'programme', 'current_course'):
        writer.writerow([
            _sf(enrolment.learner.email),
            _sf(enrolment.learner.full_name),
            enrolment.programme.code,
            enrolment.health_status,
            ', '.join(enrolment.active_flags),
            enrolment.current_course.code if enrolment.current_course else '',
            enrolment.enrolment_date or '',
            enrolment.first_sign_of_life_date or '',
            'Yes' if enrolment.is_graduated else 'No',
            enrolment.learner.payment_status,
            enrolment.last_updated_date.date() if enrolment.last_updated_date else '',
        ])
    return response


def export_interventions_csv(queryset):
    today = date.today()
    response = _csv_response(f'interventions_{today:%Y%m%d}.csv')
    writer = csv.writer(response)
    writer.writerow([
        'Date', 'Learner Email', 'Learner Name', 'Programme',
        'Type', 'Reason', 'Outcome',
        'Follow-up Required', 'Follow-up Date',
        'Notes', 'Logged By',
    ])
    for iv in queryset.select_related('learner', 'enrolment__programme', 'logged_by'):
        writer.writerow([
            iv.intervention_date,
            _sf(iv.learner.email),
            _sf(iv.learner.full_name),
            iv.enrolment.programme.code if iv.enrolment else '',
            iv.type,
            _sf(iv.reason),
            _sf(iv.outcome),
            'Yes' if iv.follow_up_required else 'No',
            iv.follow_up_date or '',
            _sf(iv.notes),
            _sf(iv.logged_by.get_full_name() or iv.logged_by.username),
        ])
    return response
