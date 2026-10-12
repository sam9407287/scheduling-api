"""
Overtime serializers
"""
from rest_framework import serializers
from .models import OvertimeRecord, OvertimeRule
from apps.employees.serializers import EmployeeListSerializer


class OvertimeRuleSerializer(serializers.ModelSerializer):
    overtime_type_display = serializers.CharField(source='get_overtime_type_display', read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    
    class Meta:
        model = OvertimeRule
        fields = [
            'id', 'organization', 'organization_name', 'overtime_type', 'overtime_type_display',
            'multiplier', 'max_hours_per_day', 'max_hours_per_month',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class OvertimeRecordAdjustSerializer(serializers.ModelSerializer):
    """管理者手動調整（PM 2026-10-13：月薪制國定假日出勤決定折發時把倍率改成 2.00）。

    只開放倍率與備註；時數、日期、類型由系統計算，不可手改。
    """

    class Meta:
        model = OvertimeRecord
        fields = ['multiplier', 'notes']


class OvertimeRecordSerializer(serializers.ModelSerializer):
    employee = EmployeeListSerializer(read_only=True)
    overtime_type_display = serializers.CharField(source='get_overtime_type_display', read_only=True)
    
    class Meta:
        model = OvertimeRecord
        fields = [
            'id', 'employee', 'attendance', 'overtime_date',
            'overtime_type', 'overtime_type_display', 'hours', 'multiplier',
            'calculated_amount', 'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
