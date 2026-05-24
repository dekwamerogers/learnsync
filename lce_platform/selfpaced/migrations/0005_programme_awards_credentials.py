from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0004_ingestionjob_progress_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='programme',
            name='awards_credentials',
            field=models.BooleanField(
                default=True,
                help_text='Uncheck for prequel/foundation programmes (e.g. WALX) that do not award badges or certificates.',
            ),
        ),
    ]
