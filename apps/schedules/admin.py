from django.contrib import admin
from django.utils import timezone
from .models import Schedule, ScheduleVersion, ScheduleChange


class ScheduleInline(admin.TabularInline):
    model = Schedule
    extra = 0
    fields = ['employee', 'shift_template', 'schedule_date', 'expected_hours', 'status']
    raw_id_fields = ['employee', 'shift_template']


class ScheduleChangeInline(admin.TabularInline):
    model = ScheduleChange
    extra = 0
    fields = ['change_type', 'original_employee', 'replacement_employee', 'reason', 'changed_by']
    raw_id_fields = ['original_employee', 'replacement_employee', 'changed_by']


@admin.register(ScheduleVersion)
class ScheduleVersionAdmin(admin.ModelAdmin):
    list_display = ['version_label', 'organization', 'version_type', 'period_start', 'period_end', 'status', 'schedule_count', 'approved_by', 'created_by']
    list_filter = ['organization', 'version_type', 'status', 'created_at']
    search_fields = ['version_label', 'organization__name']
    date_hierarchy = 'period_start'
    inlines = [ScheduleInline]
    actions = ['publish_versions', 'approve_versions', 'archive_versions']
    raw_id_fields = ['approved_by', 'created_by']

    @admin.display(description='排班數')
    def schedule_count(self, obj):
        return obj.schedules.count()

    @admin.action(description='發布選取的排班版本')
    def publish_versions(self, request, queryset):
        updated = queryset.filter(status='draft').update(status='published')
        self.message_user(request, f'已發布 {updated} 個排班版本')

    @admin.action(description='簽核選取的排班版本')
    def approve_versions(self, request, queryset):
        updated = queryset.filter(status__in=['draft', 'published']).update(
            status='approved',
            approved_by=request.user,
            approved_at=timezone.now(),
        )
        self.message_user(request, f'已簽核 {updated} 個排班版本')

    @admin.action(description='歸檔選取的排班版本')
    def archive_versions(self, request, queryset):
        updated = queryset.update(status='archived')
        self.message_user(request, f'已歸檔 {updated} 個排班版本')


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ['employee', 'schedule_date', 'shift_template', 'expected_hours', 'status', 'schedule_version']
    list_filter = ['schedule_version__version_type', 'status', 'schedule_date', 'schedule_version__organization']
    search_fields = ['employee__employee_id', 'employee__user__username']
    date_hierarchy = 'schedule_date'
    inlines = [ScheduleChangeInline]
    raw_id_fields = ['employee', 'shift_template', 'schedule_version']
    actions = ['confirm_schedules', 'cancel_schedules']

    @admin.action(description='確認選取的排班')
    def confirm_schedules(self, request, queryset):
        updated = queryset.filter(status__in=['draft', 'assigned']).update(status='confirmed')
        self.message_user(request, f'已確認 {updated} 筆排班')

    @admin.action(description='取消選取的排班')
    def cancel_schedules(self, request, queryset):
        updated = queryset.exclude(status='cancelled').update(status='cancelled')
        self.message_user(request, f'已取消 {updated} 筆排班')


@admin.register(ScheduleChange)
class ScheduleChangeAdmin(admin.ModelAdmin):
    list_display = ['schedule', 'change_type', 'original_employee', 'replacement_employee', 'changed_by', 'changed_at', 'approved_by']
    list_filter = ['change_type', 'changed_at']
    search_fields = ['schedule__employee__employee_id', 'original_employee__employee_id', 'reason']
    date_hierarchy = 'changed_at'
    raw_id_fields = ['schedule', 'original_employee', 'replacement_employee', 'changed_by', 'approved_by']
