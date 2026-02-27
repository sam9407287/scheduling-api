from django.contrib import admin
from .models import OvertimeRecord, OvertimeRule


@admin.register(OvertimeRecord)
class OvertimeRecordAdmin(admin.ModelAdmin):
    list_display = ['employee', 'overtime_date', 'overtime_type', 'hours', 'multiplier', 'calculated_amount', 'created_at']
    list_filter = ['overtime_type', 'overtime_date', 'employee__organization']
    search_fields = ['employee__employee_id', 'employee__user__username']
    date_hierarchy = 'overtime_date'
    raw_id_fields = ['employee', 'attendance']
    list_per_page = 50


@admin.register(OvertimeRule)
class OvertimeRuleAdmin(admin.ModelAdmin):
    list_display = ['organization', 'overtime_type', 'multiplier', 'max_hours_per_day', 'max_hours_per_month', 'is_active']
    list_filter = ['organization', 'overtime_type', 'is_active']
    search_fields = ['organization__name']
    actions = ['activate_rules', 'deactivate_rules']

    @admin.action(description='啟用選取的加班規則')
    def activate_rules(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'已啟用 {updated} 條加班規則')

    @admin.action(description='停用選取的加班規則')
    def deactivate_rules(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'已停用 {updated} 條加班規則')
