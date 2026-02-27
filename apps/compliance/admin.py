from django.contrib import admin
from .models import LaborLawRule, ComplianceCheck


@admin.register(LaborLawRule)
class LaborLawRuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'rule_type', 'value', 'is_active']
    list_filter = ['rule_type', 'is_active']
    search_fields = ['name']


@admin.register(ComplianceCheck)
class ComplianceCheckAdmin(admin.ModelAdmin):
    list_display = ['organization', 'check_type', 'check_period_start', 'check_period_end', 'status', 'checked_at']
    list_filter = ['organization', 'check_type', 'status', 'checked_at']
    search_fields = ['organization__name']
    date_hierarchy = 'checked_at'
