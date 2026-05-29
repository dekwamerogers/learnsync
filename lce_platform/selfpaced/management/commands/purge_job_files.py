"""
Management command: purge legacy binary CSV content from IngestionJob rows.

Before migration 0028, uploaded CSVs were stored as raw bytes in the
`file_content` BinaryField.  After ingestion completed the engine cleared it,
but failed/cancelled jobs were left with the full binary still in the column —
bloating the database.

This command zeroes out `file_content` for all jobs in terminal states
(complete, failed, cancelled) and shows how much space is reclaimed.

Usage:
    python manage.py purge_job_files            # dry-run
    python manage.py purge_job_files --apply    # actually clear
"""

from django.core.management.base import BaseCommand

# States where the CSV bytes are no longer needed and can be safely cleared.
TERMINAL_STATUSES = ('complete', 'failed', 'cancelled')


class Command(BaseCommand):
    help = 'Clear legacy file_content blobs from completed/failed/cancelled IngestionJobs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually clear the content. Default is dry-run (show only).',
        )

    def handle(self, *args, **options):
        from selfpaced.models import IngestionJob

        apply = options['apply']

        # BinaryField in MySQL stores as LONGBLOB; Django returns memoryview.
        # The only reliable "non-empty" check across databases is SQL LENGTH().
        from django.db.models import IntegerField
        from django.db.models.functions import Length
        from django.db.models import ExpressionWrapper

        qs = (
            IngestionJob.objects
            .filter(status__in=TERMINAL_STATUSES)
            .annotate(_fc_len=ExpressionWrapper(
                Length('file_content'), output_field=IntegerField()
            ))
            .filter(_fc_len__gt=0)
        )

        jobs = list(qs)
        if not jobs:
            self.stdout.write(self.style.SUCCESS(
                'No legacy file_content blobs found — database is already clean.'
            ))
            return

        total_bytes = sum(len(bytes(j.file_content)) for j in jobs)
        self.stdout.write(
            f'Found {len(jobs)} job(s) with legacy file_content '
            f'({total_bytes / 1024 / 1024:.1f} MB in the database).'
        )

        if not apply:
            self.stdout.write(self.style.WARNING(
                'Dry-run — pass --apply to actually clear the content.'
            ))
            for job in jobs[:20]:
                mb = len(bytes(job.file_content)) / 1024 / 1024
                self.stdout.write(
                    f'  #{job.pk:<5} {job.status:<12} {mb:>5.1f} MB  {job.file_name}'
                )
            if len(jobs) > 20:
                self.stdout.write(f'  … and {len(jobs) - 20} more')
            return

        # Clear in small batches so we don't hold a huge transaction open.
        cleared = 0
        for job in jobs:
            job.file_content = b''
            job.save(update_fields=['file_content'])
            cleared += 1
            if cleared % 25 == 0:
                self.stdout.write(f'  Cleared {cleared}/{len(jobs)}…')

        self.stdout.write(self.style.SUCCESS(
            f'Done — freed ~{total_bytes / 1024 / 1024:.1f} MB by clearing '
            f'file_content on {cleared} job(s).'
        ))
