from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0021_cancel_requested'),
    ]

    operations = [
        migrations.AddField(
            model_name='course',
            name='is_shared_module',
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text=(
                    'True for courses whose code appears in multiple programmes '
                    '(e.g. PF-1 through PF-5). '
                    'Completions are automatically mirrored to all enrolments that '
                    'contain a course with the same code.'
                ),
            ),
        ),
    ]
