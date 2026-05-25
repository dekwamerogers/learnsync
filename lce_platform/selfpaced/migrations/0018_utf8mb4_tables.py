"""
Convert all selfpaced app tables to utf8mb4 character set so that 4-byte
Unicode characters (emoji, mathematical script letters, etc.) can be stored
without OperationalError 1366.

This is a no-op on SQLite and PostgreSQL.

On MySQL/MariaDB the migration attempts CONVERT TO CHARACTER SET utf8mb4 on
every table.  If the host restricts charset changes on FK-referenced columns
(error 1833) the migration logs a warning and continues — the
selfpaced.parsing module already strips 4-byte chars from all CSV data before
any DB write, so the app is safe either way.  The charset upgrade is a
best-effort improvement, not a hard requirement.
"""
import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)

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

    converted = []
    skipped   = []

    with connection.cursor() as cur:
        # Attempt to disable FK checks for the session.  On some cPanel hosts
        # this is restricted; we proceed table-by-table and tolerate errors.
        try:
            cur.execute('SET SESSION foreign_key_checks = 0')
        except Exception:
            pass  # best-effort; continue anyway

        for table in _TABLES:
            try:
                cur.execute(
                    f'ALTER TABLE `{table}` '
                    f'CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'
                )
                converted.append(table)
            except Exception as exc:
                # Log but don't abort — 4-byte chars are stripped in parsing.py
                logger.warning(
                    'migration 0018: could not convert %s to utf8mb4 (%s). '
                    'App will still work; 4-byte chars are stripped at ingest time.',
                    table, exc,
                )
                skipped.append(table)

        try:
            cur.execute('SET SESSION foreign_key_checks = 1')
        except Exception:
            pass

    if converted:
        logger.info('migration 0018: converted %d tables to utf8mb4', len(converted))
    if skipped:
        logger.warning('migration 0018: skipped %d tables (see warnings above)', len(skipped))


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0017_alter_programmenamemapping_csv_name'),
    ]

    operations = [
        migrations.RunPython(_convert_to_utf8mb4, reverse_code=_noop),
    ]
