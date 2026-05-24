from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0003_review_and_delete'),
    ]

    operations = [
        migrations.AddField(
            model_name='ingestionjob',
            name='progress_log',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
