from django.contrib import admin
from .models import ShiftTemplate, ShiftRule, StaffingRequirement


@admin.register(ShiftTemplate)
class ShiftTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'organization', 'start_time', 'end_time', 'min_staff_count', 'is_active']
    list_filter = ['organization', 'is_active']
    search_fields = ['name']
    filter_horizontal = ['required_certifications']


@admin.register(ShiftRule)
class ShiftRuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'organization', 'rule_type', 'is_active']
    list_filter = ['organization', 'rule_type', 'is_active']
    search_fields = ['name']


@admin.register(StaffingRequirement)
class StaffingRequirementAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'organization', 'branch', 'shift_template', 'weekday', 'date',
                    'min_staff', 'max_staff', 'is_active']
    list_filter = ['organization', 'is_active', 'weekday']
