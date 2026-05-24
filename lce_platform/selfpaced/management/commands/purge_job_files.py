"""
management command: purge_job_files

Clears the file_content blob from completed ingestion jobs so the database
doesn't grow indefinitely with raw CSV bytes that are no longer needed.

Usage:
  python manage.py purge_job_files            # purge all complete jobs
  python manage.py purge_job_files --days 14  # only jobs older than 14 days
  python manage.py purge_job_files --dry-run  # preview without writing
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Remove stored CSV bytes from completed ingestion jobs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=0,
            help='Only purge jobs completed more than N days ago (0 = all complete jobs).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print how many rows would be affected without updating anything.',
        )

    def handle(self, *args, **options):
        from selfpaced.models import EnrolmentUploadJob, IngestionJob, PodImportJob

        days     = options['days']
        dry_run  = options['dry_run']
        cutoff   = timezone.now() - timedelta(days=days) if days else None

        total_cleared = 0

        for Model, label in [
            (IngestionJob,       'IngestionJob'),
            (EnrolmentUploadJob, 'EnrolmentUploadJob'),
            (PodImportJob,       'PodImportJob'),
        ]:
            qs = Model.objects.filter(status='complete').exclude(file_content=b'')
            if cutoff:
                qs = qs.filter(uploaded_at__lte=cutoff)

            count = qs.count()
            if not dry_run and count:
                qs.update(file_content=b'')
            total_cleared += count
            self.stdout.write(
                f'  {label}: {"would clear" if dry_run else "cleared"} {count} job(s)'
            )

        verb = 'Would clear' if dry_run else 'Cleared'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} file_content from {total_cleared} completed job(s).'
        ))
