from django.contrib import admin
from .models import Schedule, ScheduleVersion, ScheduleChange


@admin.register(ScheduleVersion)
class ScheduleVersionAdmin(admin.ModelAdmin):
    list_display = ['version_label', 'organization', 'version_type', 'period_start', 'period_end', 'status', 'approved_by']
    list_filter = ['organization', 'version_type', 'status', 'created_at']
    search_fields = ['version_label']
    date_hierarchy = 'period_start'


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ['employee', 'schedule_date', 'shift_template', 'expected_hours', 'status', 'schedule_version']
    list_filter = ['schedule_version', 'status', 'schedule_date']
    search_fields = ['employee__employee_id', 'employee__user__username']
    date_hierarchy = 'schedule_date'


@admin.register(ScheduleChange)
class ScheduleChangeAdmin(admin.ModelAdmin):
    list_display = ['schedule', 'change_type', 'original_employee', 'replacement_employee', 'changed_by', 'changed_at']
    list_filter = ['change_type', 'changed_at']
    search_fields = ['schedule__employee__employee_id', 'original_employee__employee_id']
