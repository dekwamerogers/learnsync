import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0002_ingestionjob_file_content'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ingestionjob',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('pending_review', 'Pending Review'),
                    ('processing', 'Processing'),
                    ('complete', 'Complete'),
                    ('failed', 'Failed'),
                    ('cancelled', 'Cancelled'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='ingestionjob',
            name='review_data',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='enrolment',
            name='created_by_job',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_enrolments',
                to='selfpaced.ingestionjob',
            ),
        ),
    ]
