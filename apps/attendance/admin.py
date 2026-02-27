from django.contrib import admin
from .models import Attendance, AnomalyRecord


class AnomalyInline(admin.TabularInline):
    model = AnomalyRecord
    extra = 0
    fields = ['anomaly_type', 'description', 'severity', 'resolved', 'resolved_by']
    readonly_fields = ['created_at']


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'work_date', 'clock_in', 'clock_out', 'actual_hours', 'anomaly_flag', 'is_substitute', 'is_cross_branch']
    list_filter = ['work_date', 'anomaly_flag', 'is_substitute', 'is_cross_branch']
    search_fields = ['employee__employee_id', 'employee__user__username']
    date_hierarchy = 'work_date'
    inlines = [AnomalyInline]
    raw_id_fields = ['employee', 'substitute_for', 'cross_branch']
    list_per_page = 50
    actions = ['mark_anomaly', 'clear_anomaly']

    @admin.action(description='標記為異常')
    def mark_anomaly(self, request, queryset):
        updated = queryset.update(anomaly_flag=True)
        self.message_user(request, f'已標記 {updated} 筆出勤為異常')

    @admin.action(description='清除異常標記')
    def clear_anomaly(self, request, queryset):
        updated = queryset.update(anomaly_flag=False, anomaly_reason='')
        self.message_user(request, f'已清除 {updated} 筆出勤的異常標記')


@admin.register(AnomalyRecord)
class AnomalyRecordAdmin(admin.ModelAdmin):
    list_display = ['attendance', 'anomaly_type', 'severity', 'resolved', 'resolved_by', 'created_at']
    list_filter = ['anomaly_type', 'severity', 'resolved', 'created_at']
    search_fields = ['attendance__employee__employee_id', 'description']
    actions = ['resolve_anomalies']

    @admin.action(description='處理選取的異常')
    def resolve_anomalies(self, request, queryset):
        from django.utils import timezone
        updated = queryset.filter(resolved=False).update(
            resolved=True,
            resolved_by=request.user,
            resolved_at=timezone.now(),
            resolution_notes='由管理員批次處理',
        )
        self.message_user(request, f'已處理 {updated} 筆異常')
