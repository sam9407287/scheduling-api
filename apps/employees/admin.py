from django.contrib import admin
from .models import Employee, Contract, Certification


class ContractInline(admin.TabularInline):
    model = Contract
    extra = 0
    fields = ['contract_type', 'start_date', 'end_date', 'base_salary', 'agreed_hours_per_week']
    readonly_fields = ['created_at']


@admin.register(Certification)
class CertificationAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_required', 'employee_count', 'created_at']
    list_filter = ['is_required']
    search_fields = ['name', 'code']

    @admin.display(description='持有人數')
    def employee_count(self, obj):
        return obj.employees.count()


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['employee_id', 'user', 'organization', 'branch', 'position', 'contract_type', 'is_active', 'hire_date']
    list_filter = ['organization', 'branch', 'contract_type', 'is_active', 'hire_date']
    search_fields = ['employee_id', 'user__username', 'user__email', 'user__first_name', 'user__last_name']
    filter_horizontal = ['certifications']
    inlines = [ContractInline]
    list_per_page = 30
    date_hierarchy = 'hire_date'
    actions = ['activate_employees', 'deactivate_employees']

    @admin.action(description='啟用選取的員工')
    def activate_employees(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'已啟用 {updated} 名員工')

    @admin.action(description='停用選取的員工')
    def deactivate_employees(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'已停用 {updated} 名員工')


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ['employee', 'contract_type', 'start_date', 'end_date', 'base_salary', 'agreed_hours_per_week']
    list_filter = ['contract_type', 'start_date']
    search_fields = ['employee__employee_id', 'employee__user__username']
    date_hierarchy = 'start_date'
