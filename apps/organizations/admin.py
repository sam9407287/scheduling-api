from django.contrib import admin
from .models import Organization, Branch


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'code']


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'organization', 'is_active', 'created_at']
    list_filter = ['organization', 'is_active', 'created_at']
    search_fields = ['name', 'code', 'organization__name']
