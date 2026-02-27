from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Role


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'created_at']
    search_fields = ['name']


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'firebase_uid', 'role', 'organization', 'is_active', 'created_at']
    list_filter = ['role', 'organization', 'is_active', 'is_staff', 'is_superuser']
    search_fields = ['username', 'email', 'firebase_uid']
    
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Firebase', {'fields': ('firebase_uid',)}),
        ('Organization', {'fields': ('organization', 'branch', 'role')}),
        ('Additional', {'fields': ('phone',)}),
    )
    
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Firebase', {'fields': ('firebase_uid',)}),
        ('Organization', {'fields': ('organization', 'branch', 'role')}),
    )
