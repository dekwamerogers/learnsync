"""Stub migration — the real 0025 was applied on the server but never committed.
This no-op preserves the dependency chain so local dev environments migrate cleanly."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0024_backfill_has_activity_data'),
    ]

    operations = []
