from django.db import migrations, models

AFRICAN_COUNTRIES = [
    "Algeria", "Angola", "Benin", "Botswana", "Burkina Faso", "Burundi",
    "Cabo Verde", "Cameroon", "Central African Republic", "Chad", "Comoros",
    "Congo", "Democratic Republic of Congo", "Djibouti", "Egypt",
    "Equatorial Guinea", "Eritrea", "Eswatini", "Ethiopia", "Gabon", "Gambia",
    "Ghana", "Guinea", "Guinea-Bissau", "Ivory Coast", "Côte d'Ivoire",
    "Kenya", "Lesotho", "Liberia", "Libya", "Madagascar", "Malawi", "Mali",
    "Mauritania", "Mauritius", "Morocco", "Mozambique", "Namibia", "Niger",
    "Nigeria", "Rwanda", "São Tomé and Príncipe", "Senegal", "Seychelles",
    "Sierra Leone", "Somalia", "South Africa", "South Sudan", "Sudan",
    "Tanzania", "Togo", "Tunisia", "Uganda", "Zambia", "Zimbabwe",
]


def seed_countries(apps, schema_editor):
    MonitoredCountry = apps.get_model('selfpaced', 'MonitoredCountry')
    MonitoredCountry.objects.bulk_create(
        [MonitoredCountry(name=c, is_active=True) for c in AFRICAN_COUNTRIES],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0012_enrolmentuploadjob_processing_status'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonitoredCountry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['name'], 'verbose_name_plural': 'Monitored countries'},
        ),
        migrations.RunPython(seed_countries, migrations.RunPython.noop),
    ]
