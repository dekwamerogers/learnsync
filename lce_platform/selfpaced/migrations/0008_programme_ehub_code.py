from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0007_programme_awards_certificate'),
    ]

    operations = [
        migrations.AddField(
            model_name='programme',
            name='ehub_code',
            field=models.CharField(
                blank=True,
                help_text="Alternative programme code used in eHub class names (e.g. 'CC' for COCR).",
                max_length=20,
                null=True,
                unique=True,
            ),
        ),
    ]
