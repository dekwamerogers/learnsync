from django.contrib import admin

from selfpaced.models import (
    Assignment, Course, CourseEnrolment, Enrolment, EnrolmentUploadJob,
    IngestionJob, Intervention, Learner, MonitoredCountry, Pod, PodAssignment,
    PodImportJob, Programme, ProgrammeIdentifierRegistry, ProgrammeNameMapping,
    ProgrammeThreshold,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class CourseInline(admin.TabularInline):
    model = Course
    extra = 0
    fields = ('sequence_number', 'code', 'full_name', 'expected_duration_days', 'is_active')
    ordering = ('sequence_number',)


class ProgrammeThresholdInline(admin.StackedInline):
    model = ProgrammeThreshold
    extra = 0
    can_delete = False


class ProgrammeNameMappingInline(admin.TabularInline):
    model = ProgrammeNameMapping
    extra = 1
    fields = ('csv_name',)


class AssignmentInline(admin.TabularInline):
    model = Assignment
    extra = 0
    fields = ('sequence_in_course', 'name', 'type', 'pass_threshold_pct', 'is_required_for_completion', 'is_active')
    ordering = ('sequence_in_course',)


class EnrolmentInline(admin.TabularInline):
    model = Enrolment
    extra = 0
    fields = ('programme', 'health_status', 'enrolment_date', 'first_sign_of_life_date', 'is_graduated')
    readonly_fields = ('health_status',)
    show_change_link = True


# ---------------------------------------------------------------------------
# Programme
# ---------------------------------------------------------------------------

@admin.register(Programme)
class ProgrammeAdmin(admin.ModelAdmin):
    list_display  = ('code', 'name', 'is_active', 'is_prerequisite', 'awards_credentials', 'awards_certificate', 'start_date', 'end_date')
    list_filter   = ('is_active', 'is_prerequisite', 'awards_credentials', 'awards_certificate')
    search_fields = ('code', 'name', 'ehub_code')
    ordering      = ('code',)
    inlines       = [CourseInline, ProgrammeThresholdInline, ProgrammeNameMappingInline]
    fieldsets = (
        (None, {
            'fields': ('code', 'ehub_code', 'name'),
        }),
        ('Settings', {
            'fields': ('is_active', 'is_prerequisite', 'awards_credentials', 'awards_certificate',
                       'total_courses_for_graduation'),
        }),
        ('Dates', {
            'fields': ('start_date', 'end_date'),
        }),
    )


# ---------------------------------------------------------------------------
# Course
# ---------------------------------------------------------------------------

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display  = ('__str__', 'programme', 'sequence_number', 'code', 'is_active')
    list_filter   = ('programme', 'is_active')
    search_fields = ('full_name', 'code', 'programme__code')
    ordering      = ('programme', 'sequence_number')
    inlines       = [AssignmentInline]


# ---------------------------------------------------------------------------
# Learner
# ---------------------------------------------------------------------------

@admin.register(Learner)
class LearnerAdmin(admin.ModelAdmin):
    list_display  = ('email', 'first_name', 'last_name', 'country', 'overall_health_status', 'payment_status', 'first_seen_date')
    list_filter   = ('overall_health_status', 'payment_status', 'country')
    search_fields = ('email', 'first_name', 'last_name')
    ordering      = ('last_name', 'first_name')
    readonly_fields = ('last_updated_date',)
    inlines       = [EnrolmentInline]


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------

@admin.register(Enrolment)
class EnrolmentAdmin(admin.ModelAdmin):
    list_display  = ('learner', 'programme', 'health_status', 'enrolment_date', 'first_sign_of_life_date', 'is_graduated')
    list_filter   = ('health_status', 'is_graduated', 'programme')
    search_fields = ('learner__email', 'learner__first_name', 'learner__last_name', 'programme__code')
    readonly_fields = ('health_status', 'active_flags', 'flag_detail', 'last_updated_date')
    fieldsets = (
        (None, {
            'fields': ('learner', 'programme', 'health_status'),
        }),
        ('Dates', {
            'fields': ('enrolment_date', 'first_sign_of_life_date', 'activation_date'),
        }),
        ('Graduation', {
            'fields': ('is_graduated', 'graduation_date', 'is_graduated_on_savanna'),
        }),
        ('Flags (read-only)', {
            'fields': ('active_flags', 'flag_detail'),
            'classes': ('collapse',),
        }),
    )


# ---------------------------------------------------------------------------
# MonitoredCountry
# ---------------------------------------------------------------------------

@admin.register(MonitoredCountry)
class MonitoredCountryAdmin(admin.ModelAdmin):
    list_display  = ('name', 'is_active')
    list_filter   = ('is_active',)
    search_fields = ('name',)
    list_editable = ('is_active',)
    ordering      = ('name',)


# ---------------------------------------------------------------------------
# ProgrammeNameMapping
# ---------------------------------------------------------------------------

@admin.register(ProgrammeNameMapping)
class ProgrammeNameMappingAdmin(admin.ModelAdmin):
    list_display  = ('csv_name', 'programme', 'created_at')
    list_filter   = ('programme',)
    search_fields = ('csv_name', 'programme__code')
    ordering      = ('csv_name',)


# ---------------------------------------------------------------------------
# ProgrammeIdentifierRegistry
# ---------------------------------------------------------------------------

@admin.register(ProgrammeIdentifierRegistry)
class ProgrammeIdentifierRegistryAdmin(admin.ModelAdmin):
    list_display  = ('raw_pattern', 'pattern_type', 'programme', 'course')
    list_filter   = ('pattern_type', 'programme')
    search_fields = ('raw_pattern', 'programme__code')
    ordering      = ('programme', 'raw_pattern')


# ---------------------------------------------------------------------------
# IngestionJob
# ---------------------------------------------------------------------------

@admin.register(IngestionJob)
class IngestionJobAdmin(admin.ModelAdmin):
    list_display  = ('pk', 'status', 'uploaded_at', 'uploaded_by', 'rows_processed', 'new_learners', 'updated_learners')
    list_filter   = ('status',)
    readonly_fields = ('uploaded_at', 'uploaded_by', 'status', 'rows_processed', 'new_learners',
                       'updated_learners', 'new_assignments', 'flagged_row_count', 'errors', 'warnings')
    ordering      = ('-uploaded_at',)


# ---------------------------------------------------------------------------
# EnrolmentUploadJob
# ---------------------------------------------------------------------------

@admin.register(PodImportJob)
class PodImportJobAdmin(admin.ModelAdmin):
    list_display  = ('pk', 'file_name', 'status', 'uploaded_at', 'rows_processed', 'rows_created', 'rows_updated')
    list_filter   = ('status',)
    readonly_fields = ('uploaded_at', 'uploaded_by', 'file_content', 'review_data', 'errors',
                       'rows_processed', 'rows_created', 'rows_updated', 'rows_skipped')
    ordering      = ('-uploaded_at',)


@admin.register(EnrolmentUploadJob)
class EnrolmentUploadJobAdmin(admin.ModelAdmin):
    list_display  = ('pk', 'file_name', 'status', 'uploaded_at', 'rows_processed', 'rows_created', 'rows_updated')
    list_filter   = ('status',)
    readonly_fields = ('uploaded_at', 'uploaded_by', 'file_content', 'review_data', 'errors',
                       'rows_processed', 'rows_created', 'rows_updated', 'rows_skipped')
    ordering      = ('-uploaded_at',)


# ---------------------------------------------------------------------------
# Intervention
# ---------------------------------------------------------------------------

@admin.register(Intervention)
class InterventionAdmin(admin.ModelAdmin):
    list_display  = ('learner', 'enrolment', 'type', 'outcome', 'follow_up_required', 'follow_up_date', 'logged_date')
    list_filter   = ('type', 'outcome', 'follow_up_required')
    search_fields = ('learner__email', 'learner__first_name', 'learner__last_name')
    readonly_fields = ('logged_date', 'logged_by')
    ordering      = ('-logged_date',)


# ---------------------------------------------------------------------------
# Pod
# ---------------------------------------------------------------------------

@admin.register(Pod)
class PodAdmin(admin.ModelAdmin):
    list_display  = ('name', 'programme', 'status', 'target_month')
    list_filter   = ('status', 'programme')
    search_fields = ('name', 'programme__code')


@admin.register(PodAssignment)
class PodAssignmentAdmin(admin.ModelAdmin):
    list_display  = ('pod', 'learner', 'method', 'assignment_date', 'is_current')
    list_filter   = ('method', 'pod__programme', 'is_current')
    search_fields = ('learner__email', 'pod__name')
    ordering      = ('-assignment_date',)
