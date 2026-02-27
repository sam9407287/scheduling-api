"""
AI Engine serializers
"""
from rest_framework import serializers
from datetime import date
from .providers.base import ScheduleRequest


class ScheduleRequestSerializer(serializers.Serializer):
    """排班請求序列化器"""
    organization_id = serializers.IntegerField()
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    employee_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False
    )
    shift_template_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False
    )
    constraints = serializers.JSONField(default=dict)
    preferences = serializers.JSONField(default=dict)
    
    def validate(self, data):
        if data['period_start'] > data['period_end']:
            raise serializers.ValidationError("period_start must be before period_end")
        return data


class ScheduleResultSerializer(serializers.Serializer):
    """排班結果序列化器"""
    success = serializers.BooleanField()
    assignments = serializers.ListField()
    score = serializers.FloatField()
    violations = serializers.ListField()
    metadata = serializers.DictField()
    message = serializers.CharField(required=False, allow_null=True)
