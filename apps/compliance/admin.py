from django.contrib import admin
from .models import LaborLawRule, ComplianceCheck


@admin.register(LaborLawRule)
class LaborLawRuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'rule_type', 'value', 'is_active', 'updated_at']
    list_filter = ['rule_type', 'is_active']
    search_fields = ['name', 'description']
    actions = ['activate_rules', 'deactivate_rules']

    @admin.action(description='啟用選取的規則')
    def activate_rules(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'已啟用 {updated} 條規則')

    @admin.action(description='停用選取的規則')
    def deactivate_rules(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'已停用 {updated} 條規則')


@admin.register(ComplianceCheck)
class ComplianceCheckAdmin(admin.ModelAdmin):
    list_display = ['organization', 'check_type', 'check_period_start', 'check_period_end', 'status', 'violation_count', 'checked_by', 'checked_at']
    list_filter = ['organization', 'check_type', 'status', 'checked_at']
    search_fields = ['organization__name']
    date_hierarchy = 'checked_at'
    readonly_fields = ['violations', 'warnings', 'checked_at']

    @admin.display(description='違規數')
    def violation_count(self, obj):
        return len(obj.violations) if obj.violations else 0
