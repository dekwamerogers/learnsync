from django.db import migrations, models


def set_walx_prerequisite(apps, schema_editor):
    Programme = apps.get_model('selfpaced', 'Programme')
    Programme.objects.filter(code='WALX').update(is_prerequisite=True)


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0008_programme_ehub_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='programme',
            name='is_prerequisite',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Mark for onboarding/prerequisite programmes (e.g. WALX) that run before a '
                    "learner's substantive enrolment. Excluded from headline metrics and health rollups."
                ),
            ),
        ),
        migrations.RunPython(set_walx_prerequisite, migrations.RunPython.noop),
    ]
