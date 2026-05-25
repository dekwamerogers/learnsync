"""
Convert all selfpaced app tables to utf8mb4 character set so that 4-byte
Unicode characters (emoji, mathematical script letters, etc.) can be stored
without OperationalError 1366.

This is a no-op on SQLite and PostgreSQL — only runs the ALTER on MySQL/MariaDB.
"""
from django.db import connection, migrations


# Every table created by the selfpaced app.
_TABLES = [
    'selfpaced_learner',
    'selfpaced_programme',
    'selfpaced_course',
    'selfpaced_assignment',
    'selfpaced_enrolment',
    'selfpaced_courseenrolment',
    'selfpaced_assignmentprogress',
    'selfpaced_enrolmentsnapshot',
    'selfpaced_ingestionjob',
    'selfpaced_flaggedrow',
    'selfpaced_flagcode',
    'selfpaced_programmeinputregistry',
    'selfpaced_programmenamemapping',
    'selfpaced_monitoredcountry',
    'selfpaced_enrolmentuploadjob',
    'selfpaced_podimportjob',
]


def _convert_to_utf8mb4(apps, schema_editor):
    if connection.vendor != 'mysql':
        return
    with connection.cursor() as cur:
        # Temporarily disable FK checks — MySQL refuses to alter the charset of
        # a column that is referenced by a foreign key constraint.  The ALTER
        # does not change column values or types, only the charset/collation, so
        # it is safe to skip the FK check here; we restore it immediately after.
        cur.execute('SET foreign_key_checks = 0')
        try:
            for table in _TABLES:
                cur.execute(
                    f'ALTER TABLE `{table}` '
                    f'CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'
                )
        finally:
            cur.execute('SET foreign_key_checks = 1')


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0017_alter_programmenamemapping_csv_name'),
    ]

    operations = [
        migrations.RunPython(_convert_to_utf8mb4, reverse_code=_noop),
    ]
