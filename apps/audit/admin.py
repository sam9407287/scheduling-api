from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'action', 'model_name', 'record_id', 'timestamp', 'ip_address']
    list_filter = ['action', 'model_name', 'timestamp']
    search_fields = ['user__username', 'model_name', 'record_id', 'ip_address']
    readonly_fields = [
        'user', 'action', 'model_name', 'record_id',
        'content_type', 'object_id',
        'old_data', 'new_data', 'changes',
        'ip_address', 'user_agent', 'timestamp',
    ]
    date_hierarchy = 'timestamp'
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Audit logs should never be deleted
        return False
