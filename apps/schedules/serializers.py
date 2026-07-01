"""
Schedule serializers
"""
from rest_framework import serializers
from .models import Schedule, ScheduleVersion, ScheduleChange
from apps.employees.models import Employee
from apps.employees.serializers import EmployeeListSerializer
from apps.shifts.models import ShiftTemplate
from apps.shifts.serializers import ShiftTemplateSerializer


class ScheduleSerializer(serializers.ModelSerializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    shift_template = serializers.PrimaryKeyRelatedField(queryset=ShiftTemplate.objects.all())
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = Schedule
        fields = [
            'id', 'schedule_version', 'employee', 'shift_template',
            'schedule_date', 'expected_hours', 'status', 'status_display',
            'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        schedule_version = attrs.get(
            'schedule_version',
            self.instance.schedule_version if self.instance else None,
        )
        employee = attrs.get(
            'employee',
            self.instance.employee if self.instance else None,
        )
        shift_template = attrs.get(
            'shift_template',
            self.instance.shift_template if self.instance else None,
        )

        if schedule_version and employee and employee.organization_id != schedule_version.organization_id:
            raise serializers.ValidationError({
                'employee': 'Employee must belong to the schedule version organization.'
            })

        if schedule_version and shift_template and shift_template.organization_id != schedule_version.organization_id:
            raise serializers.ValidationError({
                'shift_template': 'Shift template must belong to the schedule version organization.'
            })

        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['employee'] = EmployeeListSerializer(instance.employee).data
        data['shift_template'] = ShiftTemplateSerializer(instance.shift_template).data
        return data


class ScheduleVersionSerializer(serializers.ModelSerializer):
    version_type_display = serializers.CharField(source='get_version_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    schedule_count = serializers.IntegerField(source='schedules.count', read_only=True)
    
    class Meta:
        model = ScheduleVersion
        fields = [
            'id', 'organization', 'organization_name', 'branch', 'branch_name',
            'version_label', 'version_type', 'version_type_display',
            'period_start', 'period_end', 'status', 'status_display',
            'approved_by', 'approved_at', 'created_by', 'schedule_count',
            'derived_from', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ScheduleChangeSerializer(serializers.ModelSerializer):
    change_type_display = serializers.CharField(source='get_change_type_display', read_only=True)
    
    class Meta:
        model = ScheduleChange
        fields = [
            'id', 'schedule', 'change_type', 'change_type_display',
            'original_employee', 'replacement_employee', 'reason',
            'changed_by', 'changed_at', 'approved_by', 'approved_at'
        ]
        read_only_fields = ['id', 'changed_at']
