"""
Attendance serializers
"""
from rest_framework import serializers
from apps.employees.models import Employee
from .models import Attendance, AnomalyRecord
from apps.employees.serializers import EmployeeListSerializer


class AttendanceSerializer(serializers.ModelSerializer):
    employee = EmployeeListSerializer(read_only=True)
    # 主管替員工登記出勤用；員工自己打卡可省略（取 request.user.employee_profile）
    employee_id = serializers.PrimaryKeyRelatedField(
        source='employee', queryset=Employee.objects.all(), write_only=True, required=False,
    )

    class Meta:
        model = Attendance
        fields = [
            'id', 'employee', 'employee_id', 'work_date', 'clock_in', 'clock_out',
            'actual_hours', 'is_substitute', 'substitute_for',
            'is_cross_branch', 'cross_branch', 'anomaly_flag',
            'anomaly_reason', 'notes', 'created_at', 'updated_at'
        ]
        # anomaly_* 由後端異常檢查決定；actual_hours 在 clock_in/clock_out 齊全時由後端重算覆蓋
        read_only_fields = ['id', 'anomaly_flag', 'anomaly_reason', 'created_at', 'updated_at']
        # unique_together(employee, work_date) 會讓 DRF 把 employee_id 變成必填；
        # 改在 view.perform_create 決定 employee 後再檢查重複。
        validators = []


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
