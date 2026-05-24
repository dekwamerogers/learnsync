from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('selfpaced', '0010_programme_start_end_dates'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProgrammeNameMapping',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('csv_name', models.CharField(max_length=300, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('programme', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='name_mappings', to='selfpaced.programme')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['csv_name']},
        ),
        migrations.CreateModel(
            name='EnrolmentUploadJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('file_name', models.CharField(max_length=255)),
                ('file_content', models.BinaryField(blank=True)),
                ('status', models.CharField(choices=[('pending_review', 'Pending Review'), ('complete', 'Complete'), ('failed', 'Failed')], default='pending_review', max_length=20)),
                ('column_email', models.CharField(blank=True, max_length=200)),
                ('column_programme', models.CharField(blank=True, max_length=200)),
                ('column_date', models.CharField(blank=True, max_length=200)),
                ('rows_processed', models.PositiveIntegerField(default=0)),
                ('rows_created', models.PositiveIntegerField(default=0)),
                ('rows_updated', models.PositiveIntegerField(default=0)),
                ('rows_skipped', models.PositiveIntegerField(default=0)),
                ('review_data', models.JSONField(blank=True, default=dict)),
                ('errors', models.JSONField(blank=True, default=list)),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='enrolment_upload_jobs', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-uploaded_at']},
        ),
    ]
