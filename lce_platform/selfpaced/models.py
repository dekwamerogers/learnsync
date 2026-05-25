from django.conf import settings
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------

class AssignmentType(models.TextChoices):
    MILESTONE = 'milestone', 'Milestone'
    TEST = 'test', 'Test'
    OTHER = 'other', 'Other'


class CourseStatus(models.TextChoices):
    NOT_STARTED = 'not_started', 'Not Started'
    IN_PROGRESS = 'in_progress', 'In Progress'
    COMPLETED = 'completed', 'Completed'
    WITHDRAWN = 'withdrawn', 'Withdrawn'


class HealthStatus(models.TextChoices):
    GRADUATED = 'graduated', 'Graduated'
    DORMANT = 'dormant', 'Dormant'
    AT_RISK = 'at_risk', 'At Risk'
    ACTIVE = 'active', 'Active'
    NOT_YET_STARTED = 'not_yet_started', 'Not Yet Started'


class PaymentStatus(models.TextChoices):
    COMPLIANT = 'compliant', 'Compliant'
    DUE_SOON = 'due_soon', 'Due Soon'
    GRACE_PERIOD = 'grace_period', 'Grace Period'
    OVERDUE = 'overdue', 'Overdue'
    UNKNOWN = 'unknown', 'Unpaid'


class PatternType(models.TextChoices):
    EHUB_CLASS_NAME = 'ehub_class_name', 'eHub Class Name'
    COURSE_NAME_PREFIX = 'course_name_prefix', 'Course Name Prefix'


class InterventionType(models.TextChoices):
    CALL = 'call', 'Call'
    EMAIL = 'email', 'Email'
    SMS = 'sms', 'SMS'
    EHUB_MESSAGE = 'ehub_message', 'eHub Message'
    MEETING = 'meeting', 'Meeting'
    AUTOMATED = 'automated', 'Automated'
    OTHER = 'other', 'Other'


class InterventionOutcome(models.TextChoices):
    RE_ENGAGED = 're_engaged', 'Re-engaged'
    NO_RESPONSE = 'no_response', 'No Response'
    PROMISED_ACTION = 'promised_action', 'Promised Action'
    WITHDREW = 'withdrew', 'Learner Withdrew'
    NOT_APPLICABLE = 'not_applicable', 'Not Applicable'
    OTHER = 'other', 'Other'


class IngestionStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PENDING_REVIEW = 'pending_review', 'Pending Review'
    PROCESSING = 'processing', 'Processing'
    COMPLETE = 'complete', 'Complete'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'


class FlaggedRowResolution(models.TextChoices):
    MAPPED = 'mapped', 'Mapped to existing record'
    NEW_RECORD = 'new_record', 'Created new record'
    DISMISSED = 'dismissed', 'Dismissed'


class PaceStatus(models.TextChoices):
    ON_TRACK = 'on_track', 'On Track'
    BEHIND = 'behind', 'Behind'
    SIGNIFICANTLY_BEHIND = 'significantly_behind', 'Significantly Behind'
    AHEAD = 'ahead', 'Ahead'
    COMPLETED = 'completed', 'Completed'


class PodStatus(models.TextChoices):
    ACTIVE = 'active', 'Active'
    COMPLETED = 'completed', 'Completed'
    ARCHIVED = 'archived', 'Archived'


class PodAssignmentMethod(models.TextChoices):
    SELF_SELECTED = 'self_selected', 'Self-selected'
    AUTO_ASSIGNED = 'auto_assigned', 'Auto-assigned'
    ADMIN_ASSIGNED = 'admin_assigned', 'Admin-assigned'


# ---------------------------------------------------------------------------
# Health flag codes — used in JSONField lists on Enrolment and Snapshot
# ---------------------------------------------------------------------------

class FlagCode:
    NEVER_ACTIVATED = 'never_activated'
    INACTIVE = 'inactive'
    STUCK_ON_ASSIGNMENT = 'stuck_on_assignment'
    LOW_PASS_RATE = 'low_pass_rate'
    STALLED_BETWEEN_COURSES = 'stalled_between_courses'
    STALLED_PROGRESSION = 'stalled_progression'
    PAYMENT_ISSUE = 'payment_issue'


# ---------------------------------------------------------------------------
# Programme Catalogue
# ---------------------------------------------------------------------------

class Programme(models.Model):
    code = models.CharField(max_length=20, unique=True)
    ehub_code = models.CharField(
        max_length=20, null=True, blank=True, unique=True,
        help_text="Alternative programme code used in eHub class names (e.g. 'CC' for COCR).",
    )
    name = models.CharField(max_length=200)
    total_courses_for_graduation = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    awards_credentials = models.BooleanField(
        default=True,
        help_text="Uncheck for programmes that do not award course-completion badges.",
    )
    awards_certificate = models.BooleanField(
        default=True,
        help_text="Uncheck for programmes that do not award a graduation certificate (e.g. WALX).",
    )
    is_prerequisite = models.BooleanField(
        default=False, db_index=True,
        help_text=(
            "Mark for onboarding/prerequisite programmes (e.g. WALX) that run before a learner's "
            "substantive enrolment. Excluded from headline metrics and health rollups."
        ),
    )
    start_date = models.DateField(
        null=True, blank=True,
        help_text="Date the first cohort begins. Programmes before this date are shown as 'Upcoming'.",
    )
    end_date = models.DateField(
        null=True, blank=True,
        help_text="Date after which the programme is considered ended and excluded from views.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} — {self.name}'

    @property
    def date_status(self):
        """Returns 'upcoming', 'current', 'ended', or None (no dates set)."""
        from datetime import date as _date
        today = _date.today()
        if self.end_date and today > self.end_date:
            return 'ended'
        if self.start_date and today < self.start_date:
            return 'upcoming'
        if self.start_date or self.end_date:
            return 'current'
        return None


class Course(models.Model):
    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name='courses')
    sequence_number = models.PositiveSmallIntegerField()
    full_name = models.CharField(max_length=300)
    code = models.CharField(max_length=30, blank=True)  # e.g. AICE-2
    prerequisite_course = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='unlocks'
    )
    expected_duration_days = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        ordering = ['programme', 'sequence_number']
        unique_together = ('programme', 'sequence_number')

    def __str__(self):
        return f'{self.code or self.full_name} (seq {self.sequence_number})'


class Assignment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='assignments')
    name = models.CharField(max_length=300)
    type = models.CharField(max_length=20, choices=AssignmentType.choices, default=AssignmentType.OTHER)
    sequence_in_course = models.PositiveSmallIntegerField(default=0)
    pass_threshold_pct = models.PositiveSmallIntegerField(default=70)
    is_required_for_completion = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        ordering = ['course', 'sequence_in_course']
        unique_together = ('course', 'name')

    def __str__(self):
        return f'{self.course.code} / {self.name}'


class ProgrammeIdentifierRegistry(models.Model):
    raw_pattern = models.CharField(max_length=100, unique=True)
    pattern_type = models.CharField(max_length=30, choices=PatternType.choices)
    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name='registry_entries')
    course = models.ForeignKey(
        Course, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='registry_entries'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        verbose_name_plural = 'Programme identifier registry'

    def __str__(self):
        return f'{self.raw_pattern} → {self.programme.code}'


# ---------------------------------------------------------------------------
# Threshold Configuration
# ---------------------------------------------------------------------------

THRESHOLD_DEFAULTS = {
    'activation_threshold_days': 3,
    'inactivity_threshold_days': 7,
    'dormancy_threshold_days': 14,
    'stuck_assignment_threshold_days': 5,
    'pass_rate_threshold_pct': 70,
    'inter_course_threshold_days': 5,
    'upload_warning_threshold_days': 7,
    'pod_auto_assign_threshold_days': 14,
    'pod_behind_threshold_pct': 20,
}


class ProgrammeThreshold(models.Model):
    programme = models.OneToOneField(
        Programme, on_delete=models.CASCADE, related_name='threshold'
    )
    activation_threshold_days = models.PositiveSmallIntegerField(null=True, blank=True)
    inactivity_threshold_days = models.PositiveSmallIntegerField(null=True, blank=True)
    dormancy_threshold_days = models.PositiveSmallIntegerField(null=True, blank=True)
    stuck_assignment_threshold_days = models.PositiveSmallIntegerField(null=True, blank=True)
    pass_rate_threshold_pct = models.PositiveSmallIntegerField(null=True, blank=True)
    inter_course_threshold_days = models.PositiveSmallIntegerField(null=True, blank=True)
    upload_warning_threshold_days = models.PositiveSmallIntegerField(null=True, blank=True)
    pod_auto_assign_threshold_days = models.PositiveSmallIntegerField(null=True, blank=True)
    pod_behind_threshold_pct = models.PositiveSmallIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )

    def get(self, field):
        """Return the configured value, falling back to the system default."""
        value = getattr(self, field, None)
        return value if value is not None else THRESHOLD_DEFAULTS.get(field)

    @classmethod
    def for_programme(cls, programme):
        obj, _ = cls.objects.get_or_create(programme=programme)
        return obj

    def __str__(self):
        return f'Thresholds — {self.programme.code}'


# ---------------------------------------------------------------------------
# Learner Identity
# ---------------------------------------------------------------------------

class Learner(models.Model):
    email = models.EmailField(primary_key=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    gender = models.CharField(max_length=50, blank=True)
    country = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    ehub_profile_url = models.URLField(blank=True)
    lms_profile_url = models.URLField(blank=True)
    has_logged_into_ehub = models.BooleanField(default=False)
    has_logged_into_lms = models.BooleanField(default=False)
    has_shown_up_in_course = models.BooleanField(default=False)
    other_programmes_count = models.PositiveSmallIntegerField(default=0)
    other_programme_names = models.TextField(blank=True)
    overall_health_status = models.CharField(
        max_length=20, choices=HealthStatus.choices,
        default=HealthStatus.NOT_YET_STARTED, db_index=True,
    )
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices,
        default=PaymentStatus.UNKNOWN, db_index=True,
    )
    first_seen_date = models.DateField(null=True, blank=True, db_index=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f'{self.first_name} {self.last_name} <{self.email}>'

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------

class Enrolment(models.Model):
    learner = models.ForeignKey(Learner, on_delete=models.CASCADE, related_name='enrolments')
    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name='enrolments')
    created_by_job = models.ForeignKey(
        'IngestionJob', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='created_enrolments'
    )
    enrolment_date = models.DateField(null=True, blank=True)
    first_sign_of_life_date = models.DateField(null=True, blank=True)
    activation_date = models.DateField(null=True, blank=True)
    current_course = models.ForeignKey(
        Course, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    health_status = models.CharField(
        max_length=20, choices=HealthStatus.choices,
        default=HealthStatus.NOT_YET_STARTED, db_index=True,
    )
    # List of active flag codes, e.g. ['inactive', 'payment_issue']
    active_flags = models.JSONField(default=list)
    # Structured flag detail, e.g. {'stuck_on_assignment': {'assignment': 'Sprint 3', 'days': 8}}
    flag_detail = models.JSONField(default=dict)
    is_graduated = models.BooleanField(default=False)
    graduation_date = models.DateField(null=True, blank=True)
    is_graduated_on_savanna = models.BooleanField(default=False)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('learner', 'programme')
        ordering = ['learner', 'programme']

    def __str__(self):
        return f'{self.learner.email} / {self.programme.code}'

    @property
    def effective_start_date(self):
        """Best available start date for display.

        For prerequisite programmes (e.g. WALX): returns the oldest effective date
        across all the learner's other enrolments, since the prerequisite was completed
        before the substantive journey began.

        For regular programmes: enrolment_date → activation_date → fsol, clamped to
        programme.start_date if that is later.
        """
        from django.db.models.functions import Coalesce as _Coalesce
        learner_date = self.enrolment_date or self.activation_date or self.first_sign_of_life_date

        if self.programme.is_prerequisite:
            other_dates = list(
                self.__class__.objects
                .filter(learner_id=self.learner_id)
                .exclude(pk=self.pk)
                .annotate(_d=_Coalesce('enrolment_date', 'activation_date', 'first_sign_of_life_date'))
                .exclude(_d__isnull=True)
                .values_list('_d', flat=True)
            )
            if learner_date:
                other_dates.append(learner_date)
            return min(other_dates) if other_dates else None

        prog_date = self.programme.start_date if self.programme_id else None
        if learner_date and prog_date:
            return max(learner_date, prog_date)
        return learner_date or prog_date


class CourseEnrolment(models.Model):
    enrolment = models.ForeignKey(Enrolment, on_delete=models.CASCADE, related_name='course_enrolments')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='course_enrolments')
    opt_in_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=CourseStatus.choices,
        default=CourseStatus.NOT_STARTED
    )
    completion_date = models.DateField(null=True, blank=True)
    pass_percentage = models.FloatField(null=True, blank=True)
    is_passed = models.BooleanField(default=False)
    last_activity_date = models.DateField(null=True, blank=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('enrolment', 'course')
        ordering = ['enrolment', 'course__sequence_number']

    def __str__(self):
        return f'{self.enrolment} / {self.course.code}'


class AssignmentProgress(models.Model):
    course_enrolment = models.ForeignKey(
        CourseEnrolment, on_delete=models.CASCADE, related_name='assignment_progress'
    )
    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name='progress_records'
    )
    is_accessed = models.BooleanField(default=False)
    accessed_date = models.DateField(null=True, blank=True)
    is_submitted = models.BooleanField(default=False)
    submitted_date = models.DateField(null=True, blank=True)
    is_passed = models.BooleanField(default=False)
    passed_on_first_attempt = models.BooleanField(default=False)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('course_enrolment', 'assignment')
        ordering = ['course_enrolment', 'assignment__sequence_in_course']

    def __str__(self):
        return f'{self.course_enrolment} / {self.assignment.name}'


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

class EnrolmentSnapshot(models.Model):
    learner = models.ForeignKey(Learner, on_delete=models.CASCADE, related_name='snapshots')
    enrolment = models.ForeignKey(Enrolment, on_delete=models.CASCADE, related_name='snapshots')
    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name='snapshots')
    ingestion_job = models.ForeignKey(
        'IngestionJob', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='snapshots'
    )
    snapshot_date = models.DateField()
    current_course_sequence = models.PositiveSmallIntegerField(null=True, blank=True)
    courses_completed = models.PositiveSmallIntegerField(default=0)
    assignments_accessed = models.PositiveSmallIntegerField(default=0)
    assignments_submitted = models.PositiveSmallIntegerField(default=0)
    assignments_passed = models.PositiveSmallIntegerField(default=0)
    pass_rate = models.FloatField(null=True, blank=True)
    last_activity_date = models.DateField(null=True, blank=True)
    days_since_last_activity = models.PositiveSmallIntegerField(null=True, blank=True)
    days_since_first_sign_of_life = models.PositiveSmallIntegerField(null=True, blank=True)
    health_status = models.CharField(max_length=20, choices=HealthStatus.choices)
    active_flags = models.JSONField(default=list)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, blank=True)
    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='deleted_sp_snapshots'
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['enrolment', '-snapshot_date']

    def __str__(self):
        return f'Snapshot {self.enrolment} @ {self.snapshot_date}'

    def soft_delete(self, user):
        self.is_deleted = True
        self.deleted_by = user
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_by', 'deleted_at'])


# ---------------------------------------------------------------------------
# Intervention
# ---------------------------------------------------------------------------

class Intervention(models.Model):
    learner = models.ForeignKey(Learner, on_delete=models.CASCADE, related_name='interventions')
    enrolment = models.ForeignKey(
        Enrolment, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='interventions'
    )
    intervention_date = models.DateField()
    logged_date = models.DateTimeField(auto_now_add=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='sp_interventions'
    )
    type = models.CharField(max_length=20, choices=InterventionType.choices)
    reason = models.CharField(max_length=200, blank=True)
    outcome = models.CharField(max_length=20, choices=InterventionOutcome.choices)
    follow_up_required = models.BooleanField(default=False)
    follow_up_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    # Opaque reference set only by initiative modules — never by core views.
    initiative_id = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['-intervention_date', '-logged_date']

    def __str__(self):
        return f'{self.type} / {self.learner.email} @ {self.intervention_date}'


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class IngestionJob(models.Model):
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='sp_ingestion_jobs'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    file_name = models.CharField(max_length=255)
    file_content = models.BinaryField(blank=True)
    status = models.CharField(
        max_length=20, choices=IngestionStatus.choices,
        default=IngestionStatus.PENDING
    )
    # The date the CSV data was extracted from the source system.
    # Health flags (days since activity, days since FSOL, etc.) are calculated
    # relative to this date — NOT date.today() — so that uploading an old CSV
    # does not make learners appear artificially dormant or at-risk.
    data_as_of_date = models.DateField(null=True, blank=True)
    rows_processed = models.PositiveIntegerField(default=0)
    new_learners = models.PositiveIntegerField(default=0)
    updated_learners = models.PositiveIntegerField(default=0)
    new_assignments = models.PositiveIntegerField(default=0)
    flagged_row_count = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list)
    errors = models.JSONField(default=list)
    review_data = models.JSONField(default=dict, blank=True)
    progress_log = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'IngestionJob #{self.pk} — {self.file_name} ({self.status})'


class FlaggedRow(models.Model):
    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name='flagged_rows')
    raw_data = models.JSONField()
    flag_reason = models.CharField(max_length=100)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(
        max_length=20, choices=FlaggedRowResolution.choices, blank=True
    )

    class Meta:
        ordering = ['job', 'id']

    def __str__(self):
        return f'FlaggedRow #{self.pk} ({self.flag_reason})'

    @property
    def is_resolved(self):
        return bool(self.resolution)


# ---------------------------------------------------------------------------
# Monitored countries
# ---------------------------------------------------------------------------

class MonitoredCountry(models.Model):
    """African countries the system tracks. Rows from other countries are excluded during ingestion."""
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Monitored countries'

    def __str__(self):
        return self.name

    @classmethod
    def active_names_lower(cls) -> set:
        """Return a set of lowercase active country names for fast membership testing."""
        return {n.lower() for n in cls.objects.filter(is_active=True).values_list('name', flat=True)}


# ---------------------------------------------------------------------------
# Programme Name Mapping (enrolment CSV upload)
# ---------------------------------------------------------------------------

class ProgrammeNameMapping(models.Model):
    """Maps a programme name string (as it appears in an enrolment CSV) to a Programme."""
    csv_name = models.CharField(max_length=255, unique=True)
    programme = models.ForeignKey(
        Programme, on_delete=models.CASCADE, related_name='name_mappings'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['csv_name']

    def __str__(self):
        return f'"{self.csv_name}" -> {self.programme.code}'


class EnrolmentUploadJob(models.Model):
    """Tracks a programme-enrolment CSV upload (separate from the activity-data IngestionJob)."""
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='enrolment_upload_jobs'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    file_name = models.CharField(max_length=255)
    file_content = models.BinaryField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending_review', 'Pending Review'),
            ('processing', 'Processing'),
            ('complete', 'Complete'),
            ('failed', 'Failed'),
        ],
        default='pending_review',
    )
    column_email = models.CharField(max_length=200, blank=True)
    column_programme = models.CharField(max_length=200, blank=True)
    column_date = models.CharField(max_length=200, blank=True)
    rows_processed = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    review_data = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'EnrolmentUpload #{self.pk} — {self.file_name} ({self.status})'


# ---------------------------------------------------------------------------
# Pod Import (Google Form CSV)
# ---------------------------------------------------------------------------

class PodImportJob(models.Model):
    """Tracks a Google-Form pod-selection CSV upload."""
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='pod_import_jobs'
    )
    uploaded_at    = models.DateTimeField(auto_now_add=True)
    file_name      = models.CharField(max_length=255)
    file_content   = models.BinaryField(blank=True)
    status         = models.CharField(
        max_length=20,
        choices=[
            ('pending_review', 'Pending Review'),
            ('processing',     'Processing'),
            ('complete',       'Complete'),
            ('failed',         'Failed'),
        ],
        default='pending_review',
    )
    # Column selections saved at review time
    column_email         = models.CharField(max_length=200, blank=True)
    column_programme     = models.CharField(max_length=200, blank=True)
    column_target_month  = models.CharField(max_length=200, blank=True)
    column_enrol_month   = models.CharField(max_length=200, blank=True)
    # Counters
    rows_processed = models.PositiveIntegerField(default=0)
    rows_created   = models.PositiveIntegerField(default=0)
    rows_updated   = models.PositiveIntegerField(default=0)
    rows_skipped   = models.PositiveIntegerField(default=0)
    review_data    = models.JSONField(default=dict, blank=True)
    errors         = models.JSONField(default=list,  blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'PodImport #{self.pk} — {self.file_name} ({self.status})'


# ---------------------------------------------------------------------------
# Pod Module
# ---------------------------------------------------------------------------

class Pod(models.Model):
    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name='pods')
    name = models.CharField(max_length=100)
    # Last day of the target month
    target_month = models.DateField()
    status = models.CharField(
        max_length=20, choices=PodStatus.choices,
        default=PodStatus.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        ordering = ['programme', 'target_month']
        unique_together = ('programme', 'target_month')

    def __str__(self):
        return f'{self.name} ({self.programme.code} / {self.target_month:%b %Y})'


class PodAssignment(models.Model):
    learner = models.ForeignKey(Learner, on_delete=models.CASCADE, related_name='pod_assignments')
    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name='pod_assignments')
    pod = models.ForeignKey(Pod, on_delete=models.CASCADE, related_name='assignments')
    assignment_date = models.DateField(auto_now_add=True)
    method = models.CharField(max_length=20, choices=PodAssignmentMethod.choices)
    is_current = models.BooleanField(default=True)
    previous_pod = models.ForeignKey(
        Pod, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='previous_assignments'
    )
    pod_switch_date = models.DateField(null=True, blank=True)
    pod_switch_reason = models.TextField(blank=True)
    switch_logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    # Pace fields — computed on every ingestion
    current_pace = models.FloatField(null=True, blank=True)   # courses per day
    required_pace = models.FloatField(null=True, blank=True)  # courses per day
    pace_status = models.CharField(
        max_length=25, choices=PaceStatus.choices, blank=True
    )
    courses_behind = models.FloatField(null=True, blank=True)
    projected_completion_date = models.DateField(null=True, blank=True)
    last_computed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['programme', 'learner']

    def __str__(self):
        return f'{self.learner.email} → {self.pod.name} ({"current" if self.is_current else "past"})'
