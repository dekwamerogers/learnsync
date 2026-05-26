"""
Data migration: backfill has_activity_data=True on Enrolment records that were
processed by the activity ingestion engine (i.e. have at least one CourseEnrolment).

This runs automatically as part of `python manage.py migrate` — no extra script
needed on the server.
"""
from django.db import migrations


def backfill_has_activity_data(apps, schema_editor):
    Enrolment = apps.get_model('selfpaced', 'Enrolment')
    CourseEnrolment = apps.get_model('selfpaced', 'CourseEnrolment')

    # Any enrolment that has at least one CE was processed by the activity engine.
    activity_pks = (
        CourseEnrolment.objects
        .values_list('enrolment_id', flat=True)
        .distinct()
    )
    Enrolment.objects.filter(
        pk__in=activity_pks, has_activity_data=False
    ).update(has_activity_data=True)


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0023_enrolment_has_activity_data'),
    ]

    operations = [
        migrations.RunPython(
            backfill_has_activity_data,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
