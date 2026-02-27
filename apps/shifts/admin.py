from django.contrib import admin
from .models import ShiftTemplate, ShiftRule


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
