"""
Leave serializers
"""
from rest_framework import serializers
from .models import LeaveRequest


class LeaveRequestSerializer(serializers.ModelSerializer):
    leave_type_display = serializers.CharField(source='get_leave_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_id', read_only=True)
    employee_name = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')
    reviewed_by_name = serializers.CharField(source='reviewed_by.username', read_only=True, default='')
    total_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'organization', 'employee', 'employee_code', 'employee_name',
            'leave_type', 'leave_type_display', 'start_date', 'end_date',
            'request_unit', 'start_time', 'end_time',
            'total_days', 'reason', 'status', 'status_display',
            'submission_source',
            'created_by', 'created_by_name', 'reviewed_by', 'reviewed_by_name',
            'reviewed_at', 'review_note', 'affected_schedule_ids',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'organization', 'status', 'submission_source', 'created_by', 'reviewed_by',
            'reviewed_at', 'review_note', 'affected_schedule_ids',
            'created_at', 'updated_at'
        ]

    def get_employee_name(self, obj):
        user = obj.employee.user
        return f"{user.last_name}{user.first_name}" if user else obj.employee.employee_id

    def validate(self, attrs):
        start = attrs.get('start_date')
        end = attrs.get('end_date')
        if start and end and end < start:
            raise serializers.ValidationError({'end_date': 'end_date must not be before start_date'})

        # 小時制簡化版限制：單日、同日 start<end（不跨午夜）
        if attrs.get('request_unit') == 'time_range':
            start_time = attrs.get('start_time')
            end_time = attrs.get('end_time')
            if not start_time or not end_time:
                raise serializers.ValidationError(
                    {'start_time': 'start_time and end_time are required for time_range'})
            if start != end:
                raise serializers.ValidationError(
                    {'end_date': 'time_range leave must be a single day (start_date == end_date)'})
            if end_time <= start_time:
                raise serializers.ValidationError(
                    {'end_time': 'end_time must be after start_time (cross-midnight not supported)'})
        return attrs
