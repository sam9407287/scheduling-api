from django.contrib import admin
from .models import Attendance, AnomalyRecord


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'work_date', 'clock_in', 'clock_out', 'actual_hours', 'anomaly_flag', 'is_substitute']
    list_filter = ['work_date', 'anomaly_flag', 'is_substitute', 'is_cross_branch']
    search_fields = ['employee__employee_id', 'employee__user__username']
    date_hierarchy = 'work_date'


@admin.register(AnomalyRecord)
class AnomalyRecordAdmin(admin.ModelAdmin):
    list_display = ['attendance', 'anomaly_type', 'severity', 'resolved', 'resolved_by', 'created_at']
    list_filter = ['anomaly_type', 'severity', 'resolved', 'created_at']
    search_fields = ['attendance__employee__employee_id']
