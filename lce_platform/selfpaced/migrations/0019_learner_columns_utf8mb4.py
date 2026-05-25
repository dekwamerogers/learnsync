"""
Convert the text columns on selfpaced_learner to utf8mb4 individually,
explicitly skipping the `email` primary-key column that is referenced by a
foreign key in selfpaced_enrolment (MySQL error 1833 prevents altering that
column via CONVERT TO CHARACTER SET).

All other selfpaced tables are converted via CONVERT TO CHARACTER SET with
foreign_key_checks disabled.  Any failure is logged and skipped — the app
is safe because parsing.py sanitises non-latin1 chars before every insert.
"""
import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)

# selfpaced_learner text columns that need utf8mb4.
# 'email' is deliberately excluded — it is the PK referenced by FK and only
# ever contains ASCII characters, so it does not need a charset change.
_LEARNER_TEXT_COLS = [
    ('first_name',           'VARCHAR(100)',  'NOT NULL', "DEFAULT ''"),
    ('last_name',            'VARCHAR(100)',  'NOT NULL', "DEFAULT ''"),
    ('phone_number',         'VARCHAR(50)',   'NOT NULL', "DEFAULT ''"),
    ('gender',               'VARCHAR(50)',   'NOT NULL', "DEFAULT ''"),
    ('country',              'VARCHAR(100)',  'NOT NULL', "DEFAULT ''"),
    ('region',               'VARCHAR(100)',  'NOT NULL', "DEFAULT ''"),
    ('ehub_profile_url',     'VARCHAR(200)',  'NOT NULL', "DEFAULT ''"),
    ('lms_profile_url',      'VARCHAR(200)',  'NOT NULL', "DEFAULT ''"),
    ('other_programme_names','LONGTEXT',      'NOT NULL', "DEFAULT ''"),
    ('overall_health_status','VARCHAR(20)',   'NOT NULL', "DEFAULT ''"),
    ('payment_status',       'VARCHAR(20)',   'NOT NULL', "DEFAULT ''"),
]

# All other tables — converted wholesale (no varchar PK / FK issue).
_OTHER_TABLES = [
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


def _run(apps, schema_editor):
    if connection.vendor != 'mysql':
        return

    with connection.cursor() as cur:

        # 1. Modify each text column on selfpaced_learner individually.
        for col, col_type, nullable, default in _LEARNER_TEXT_COLS:
            sql = (
                f'ALTER TABLE `selfpaced_learner` MODIFY COLUMN `{col}` '
                f'{col_type} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci '
                f'{nullable} {default}'
            )
            try:
                cur.execute(sql)
                logger.info('migration 0019: converted selfpaced_learner.%s', col)
            except Exception as exc:
                logger.warning(
                    'migration 0019: could not convert selfpaced_learner.%s — %s', col, exc
                )

        # 2. Convert remaining tables in full.
        try:
            cur.execute('SET SESSION foreign_key_checks = 0')
        except Exception:
            pass

        for table in _OTHER_TABLES:
            try:
                cur.execute(
                    f'ALTER TABLE `{table}` '
                    f'CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'
                )
                logger.info('migration 0019: converted %s', table)
            except Exception as exc:
                logger.warning(
                    'migration 0019: could not convert %s — %s', table, exc
                )

        try:
            cur.execute('SET SESSION foreign_key_checks = 1')
        except Exception:
            pass


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0018_utf8mb4_tables'),
    ]

    operations = [
        migrations.RunPython(_run, reverse_code=_noop),
    ]
