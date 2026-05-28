from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0026_fix_prerequisite_programme_health'),
    ]

    operations = [
        migrations.AddField(
            model_name='podassignment',
            name='learning_velocity',
            field=models.FloatField(
                blank=True,
                null=True,
                help_text=(
                    'Inter-completion velocity (c/week): (completions − 1) ÷ '
                    'weeks(first_completion, last_completion). Captures active '
                    'learning pace independent of current dormancy. '
                    'None when fewer than 2 courses completed.'
                ),
            ),
        ),
    ]
