from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0020_ingestionjob_data_as_of_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='ingestionjob',
            name='cancel_requested',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='enrolmentuploadjob',
            name='cancel_requested',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='podimportjob',
            name='cancel_requested',
            field=models.BooleanField(default=False),
        ),
    ]
