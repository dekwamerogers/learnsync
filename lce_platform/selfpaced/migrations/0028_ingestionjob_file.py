from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0027_podassignment_learning_velocity'),
    ]

    operations = [
        migrations.AddField(
            model_name='ingestionjob',
            name='file',
            field=models.FileField(blank=True, null=True, upload_to='ingestion_csvs/'),
        ),
    ]
