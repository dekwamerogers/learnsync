from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0022_course_is_shared_module'),
    ]

    operations = [
        migrations.AddField(
            model_name='enrolment',
            name='has_activity_data',
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text='Set to True when this enrolment first appears in an activity CSV.',
            ),
        ),
    ]
