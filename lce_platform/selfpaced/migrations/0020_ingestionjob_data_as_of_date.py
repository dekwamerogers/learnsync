from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0019_learner_columns_utf8mb4'),
    ]

    operations = [
        migrations.AddField(
            model_name='ingestionjob',
            name='data_as_of_date',
            field=models.DateField(
                null=True,
                blank=True,
                help_text=(
                    'The date the CSV data was extracted from the source system. '
                    'Health flags are calculated relative to this date, not today.'
                ),
            ),
        ),
    ]
