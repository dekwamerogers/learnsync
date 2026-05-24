"""
Django Q background tasks for the self-paced ingestion pipeline.
"""

import logging

logger = logging.getLogger(__name__)


def process_ingestion_job(job_id: int) -> None:
    """
    Entry point called by Django Q after a CSV is uploaded and queued.
    Delegates to the engine, then marks the job complete or failed.
    """
    from selfpaced.engine import run_ingestion
    from selfpaced.models import IngestionJob

    logger.info('SP ingestion job %d starting', job_id)
    try:
        run_ingestion(job_id)
        logger.info('SP ingestion job %d complete', job_id)
    except Exception:
        logger.exception('SP ingestion job %d failed', job_id)
        # Status already set to 'failed' inside run_ingestion — nothing more to do
