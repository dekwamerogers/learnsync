from django.db import migrations


def set_walx_no_credentials(apps, schema_editor):
    Programme = apps.get_model('selfpaced', 'Programme')
    Programme.objects.filter(code='WALX').update(awards_credentials=False)


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0005_programme_awards_credentials'),
    ]

    operations = [
        migrations.RunPython(set_walx_no_credentials, migrations.RunPython.noop),
    ]
