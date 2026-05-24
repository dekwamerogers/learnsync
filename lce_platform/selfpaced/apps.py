from django.apps import AppConfig


class SelfpacedConfig(AppConfig):
    name = 'selfpaced'

    def ready(self):
        import selfpaced.signals  # noqa: F401
        self._reset_orphaned_jobs()

    def _reset_orphaned_jobs(self):
        """
        Any job still marked 'processing' at startup was running in a daemon thread
        that died when the server restarted. Reset them to 'failed' so they can be
        retried — otherwise they stay stuck in processing forever.
        """
        try:
            from selfpaced.models import EnrolmentUploadJob, IngestionJob, PodImportJob
            msg = ['Job was mid-run when the server restarted. Use Retry / Reprocess to re-run it.']
            IngestionJob.objects.filter(status='processing').update(status='failed', errors=msg)
            EnrolmentUploadJob.objects.filter(status='processing').update(status='failed', errors=msg)
            PodImportJob.objects.filter(status='processing').update(status='failed', errors=msg)
        except Exception:
            pass  # tables may not exist yet (pre-migration first run)
