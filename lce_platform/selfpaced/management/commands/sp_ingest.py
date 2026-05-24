"""
Management command: sp_ingest

Usage:
    python manage.py sp_ingest               # process all pending jobs
    python manage.py sp_ingest <job_pk>      # process a specific job by PK
    python manage.py sp_ingest --file path   # ingest a CSV file directly

Examples:
    python manage.py sp_ingest
    python manage.py sp_ingest 3
    python manage.py sp_ingest --file /tmp/learners.csv
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Run the self-paced ingestion engine synchronously.'

    def add_arguments(self, parser):
        parser.add_argument(
            'job_pk', nargs='?', type=int,
            help='PK of a specific IngestionJob to process.',
        )
        parser.add_argument(
            '--file', metavar='PATH',
            help='Path to a CSV file to ingest directly (creates a new job).',
        )
        parser.add_argument(
            '--all-pending', action='store_true',
            help='Process all jobs with status=pending.',
        )

    def handle(self, *args, **options):
        from selfpaced.engine import run_ingestion
        from selfpaced.models import IngestionJob

        if options['file']:
            self._ingest_file(options['file'], run_ingestion)
        elif options['job_pk']:
            self._run_job(options['job_pk'], run_ingestion)
        else:
            # Default: process all pending, or show status
            pending = IngestionJob.objects.filter(status='pending').order_by('id')
            if not pending.exists():
                self.stdout.write('No pending jobs.')
                self._print_recent()
                return
            for job in pending:
                self._run_job(job.pk, run_ingestion)

    def _run_job(self, pk, run_ingestion):
        from selfpaced.models import IngestionJob
        try:
            job = IngestionJob.objects.get(pk=pk)
        except IngestionJob.DoesNotExist:
            raise CommandError(f'Job {pk} not found.')

        self.stdout.write(f'Processing job #{pk} ({job.file_name})...')
        try:
            run_ingestion(pk)
            job.refresh_from_db()
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ {job.status} — {job.rows_processed} rows, '
                f'{job.new_learners} new learners, '
                f'{job.flagged_row_count} flagged rows'
            ))
            if job.errors:
                for e in job.errors:
                    self.stderr.write(f'  ERROR: {e}')
            if job.warnings:
                self.stdout.write(f'  {len(job.warnings)} warnings')
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'  ✗ FAILED: {exc}'))

    def _ingest_file(self, path, run_ingestion):
        import os
        from selfpaced.models import IngestionJob

        if not os.path.exists(path):
            raise CommandError(f'File not found: {path}')

        with open(path, 'rb') as fh:
            content = fh.read()

        User = get_user_model()
        user = User.objects.filter(is_superuser=True).first()
        job = IngestionJob.objects.create(
            uploaded_by=user,
            file_name=os.path.basename(path),
            file_content=content,
        )
        self.stdout.write(f'Created job #{job.pk} from {path}')
        self._run_job(job.pk, run_ingestion)

    def _print_recent(self):
        from selfpaced.models import IngestionJob
        jobs = IngestionJob.objects.order_by('-uploaded_at')[:5]
        if jobs:
            self.stdout.write('\nRecent jobs:')
            for j in jobs:
                self.stdout.write(f'  #{j.pk} {j.file_name} [{j.status}] '
                                  f'{j.rows_processed} rows, {j.flagged_row_count} flagged')
