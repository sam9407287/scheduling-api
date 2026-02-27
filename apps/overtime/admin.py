from django.contrib import admin
from .models import OvertimeRecord, OvertimeRule


@admin.register(OvertimeRecord)
class OvertimeRecordAdmin(admin.ModelAdmin):
    list_display = ['employee', 'overtime_date', 'overtime_type', 'hours', 'multiplier', 'calculated_amount']
    list_filter = ['overtime_type', 'overtime_date']
    search_fields = ['employee__employee_id']
    date_hierarchy = 'overtime_date'


@admin.register(OvertimeRule)
class OvertimeRuleAdmin(admin.ModelAdmin):
    list_display = ['organization', 'overtime_type', 'multiplier', 'max_hours_per_day', 'is_active']
    list_filter = ['organization', 'overtime_type', 'is_active']
    search_fields = ['organization__name']
