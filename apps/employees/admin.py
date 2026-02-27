from django.contrib import admin
from .models import Employee, Contract, Certification


@admin.register(Certification)
class CertificationAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_required', 'created_at']
    list_filter = ['is_required']
    search_fields = ['name', 'code']


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['employee_id', 'user', 'organization', 'branch', 'position', 'contract_type', 'is_active']
    list_filter = ['organization', 'branch', 'contract_type', 'is_active']
    search_fields = ['employee_id', 'user__username', 'user__email']
    filter_horizontal = ['certifications']


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ['employee', 'contract_type', 'start_date', 'end_date', 'base_salary']
    list_filter = ['contract_type', 'start_date']
    search_fields = ['employee__employee_id', 'employee__user__username']
