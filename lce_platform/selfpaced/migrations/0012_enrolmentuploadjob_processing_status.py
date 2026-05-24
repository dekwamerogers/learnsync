from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0011_enrolment_upload'),
    ]

    operations = [
        migrations.AlterField(
            model_name='enrolmentuploadjob',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending_review', 'Pending Review'),
                    ('processing', 'Processing'),
                    ('complete', 'Complete'),
                    ('failed', 'Failed'),
                ],
                default='pending_review',
                max_length=20,
            ),
        ),
    ]
