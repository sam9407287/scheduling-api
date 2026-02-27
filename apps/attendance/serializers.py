"""
Attendance serializers
"""
from rest_framework import serializers
from .models import Attendance, AnomalyRecord
from apps.employees.serializers import EmployeeListSerializer


class AttendanceSerializer(serializers.ModelSerializer):
    employee = EmployeeListSerializer(read_only=True)
    
    class Meta:
        model = Attendance
        fields = [
            'id', 'employee', 'work_date', 'clock_in', 'clock_out',
            'actual_hours', 'is_substitute', 'substitute_for',
            'is_cross_branch', 'cross_branch', 'anomaly_flag',
            'anomaly_reason', 'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AnomalyRecordSerializer(serializers.ModelSerializer):
    anomaly_type_display = serializers.CharField(source='get_anomaly_type_display', read_only=True)
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    
    class Meta:
        model = AnomalyRecord
        fields = [
            'id', 'attendance', 'anomaly_type', 'anomaly_type_display',
            'description', 'severity', 'severity_display',
            'resolved', 'resolved_by', 'resolved_at', 'resolution_notes',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at']
