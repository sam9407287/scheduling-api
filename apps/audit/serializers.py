"""
Audit serializers — read-only exposure of AuditLog for the 操作日誌 page.
"""
from rest_framework import serializers
from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            'id', 'user', 'user_name', 'action', 'action_display',
            'model_name', 'record_id', 'changes', 'timestamp',
        ]

    def get_user_name(self, obj):
        if obj.user is None:
            return '系統'
        full = obj.user.get_full_name()
        return full or obj.user.username


class AuditLogDetailSerializer(AuditLogSerializer):
    """Detail view additionally exposes the full before/after snapshots."""

    class Meta(AuditLogSerializer.Meta):
        fields = AuditLogSerializer.Meta.fields + [
            'old_data', 'new_data', 'ip_address', 'user_agent',
        ]
