from django.db import migrations, models


def set_walx_no_certificate(apps, schema_editor):
    Programme = apps.get_model('selfpaced', 'Programme')
    # WALX can award badges but not graduation certificates
    Programme.objects.filter(code='WALX').update(awards_certificate=False)
    # Programmes that previously had awards_credentials=False get neither
    Programme.objects.filter(awards_credentials=False).update(awards_certificate=False)


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0006_walx_no_credentials'),
    ]

    operations = [
        migrations.AddField(
            model_name='programme',
            name='awards_certificate',
            field=models.BooleanField(
                default=True,
                help_text='Uncheck for programmes that do not award a graduation certificate (e.g. WALX).',
            ),
        ),
        migrations.RunPython(set_walx_no_certificate, migrations.RunPython.noop),
    ]
