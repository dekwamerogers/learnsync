from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0009_programme_is_prerequisite'),
    ]

    operations = [
        migrations.AddField(
            model_name='programme',
            name='start_date',
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Date the first cohort begins. Programmes before this date are shown as 'Upcoming'.",
            ),
        ),
        migrations.AddField(
            model_name='programme',
            name='end_date',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='Date after which the programme is considered ended and excluded from views.',
            ),
        ),
    ]
