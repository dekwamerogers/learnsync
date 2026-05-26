"""
Data migration: fix health_status / flags for prerequisite-programme enrolments
(e.g. WALX) that were ingested before the programme was flagged as
is_prerequisite=True.

Those enrolments accumulated health flags (dormant, at_risk, inactive, etc.)
that should never apply to a prerequisite programme.  This migration:

  • Marks an enrolment as graduated + clears flags where every CourseEnrolment
    is passed (is_passed=True or status='completed'), OR where is_graduated is
    already True and no CEs exist (auto-graduation case).

  • For enrolments whose courses are NOT yet all passed, simply clears any
    stale warning flags (they keep their existing health_status value).

Runs automatically as part of `python manage.py migrate`.
After running the migration, also trigger "Recompute Health" from the admin
panel so the learner-level overall_health_status rolls up correctly.
"""
from django.db import migrations


def fix_prerequisite_health(apps, schema_editor):
    Enrolment = apps.get_model('selfpaced', 'Enrolment')
    CourseEnrolment = apps.get_model('selfpaced', 'CourseEnrolment')
    Programme = apps.get_model('selfpaced', 'Programme')

    prereq_progs = list(Programme.objects.filter(is_prerequisite=True))
    if not prereq_progs:
        return

    # Pre-fetch all CEs for these programmes in two queries (avoid N+1)
    prereq_prog_pks = {p.pk for p in prereq_progs}

    enrolments = list(
        Enrolment.objects.filter(programme_id__in=prereq_prog_pks)
    )
    if not enrolments:
        return

    enrolment_pks = {e.pk for e in enrolments}

    # Map enrolment_pk → list of CourseEnrolments
    from collections import defaultdict
    ces_by_enrolment = defaultdict(list)
    for ce in CourseEnrolment.objects.filter(enrolment_id__in=enrolment_pks):
        ces_by_enrolment[ce.enrolment_id].append(ce)

    to_graduate = []   # need is_graduated + health_status + flags update
    to_clear    = []   # only need flags cleared (not yet graduated)

    for e in enrolments:
        ces = ces_by_enrolment.get(e.pk, [])

        if not ces:
            # No course activity — either auto-graduated (is_graduated=True via
            # Phase 4c) or enrollment-CSV-only (has_activity_data=False).
            if e.is_graduated:
                if e.health_status != 'graduated' or e.active_flags:
                    e.health_status = 'graduated'
                    e.active_flags  = []
                    e.flag_detail   = {}
                    to_graduate.append(e)
            else:
                # Enrollment-only: reset any stale flags; keep not_yet_started.
                if e.active_flags or e.health_status not in ('not_yet_started', 'graduated'):
                    e.health_status = 'not_yet_started'
                    e.active_flags  = []
                    e.flag_detail   = {}
                    to_clear.append(e)
        else:
            # Has course enrolments — graduated if every course is passed.
            all_passed = all(
                ce.status == 'completed' or ce.is_passed
                for ce in ces
            )
            if all_passed:
                if (not e.is_graduated
                        or e.health_status != 'graduated'
                        or e.active_flags):
                    e.is_graduated  = True
                    e.health_status = 'graduated'
                    e.active_flags  = []
                    e.flag_detail   = {}
                    to_graduate.append(e)
            else:
                # Partially complete — clear stale warning flags but leave
                # health_status alone (may be legitimately active/at_risk).
                if e.active_flags:
                    e.active_flags = []
                    e.flag_detail  = {}
                    to_clear.append(e)

    if to_graduate:
        Enrolment.objects.bulk_update(
            to_graduate,
            ['is_graduated', 'health_status', 'active_flags', 'flag_detail'],
            batch_size=500,
        )
    if to_clear:
        Enrolment.objects.bulk_update(
            to_clear,
            ['health_status', 'active_flags', 'flag_detail'],
            batch_size=500,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0025_alter_ingestionjob_data_as_of_date'),
    ]

    operations = [
        migrations.RunPython(
            fix_prerequisite_health,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
