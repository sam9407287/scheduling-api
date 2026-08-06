"""
Schedule serializers
"""
from rest_framework import serializers
from .models import Schedule, ScheduleVersion, ScheduleChange, ScheduleOverlapDecision
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

    @staticmethod
    def _expand_version_period(schedule):
        """班次落在版本資料範圍外時向外擴張範圍（只擴不縮）。

        period 是資料涵蓋範圍而非排班限制——任何日期都能排班。
        filter 條件確保並發時只有真正需要擴張的 update 會生效。
        """
        version = schedule.schedule_version
        day = schedule.schedule_date
        ScheduleVersion.objects.filter(
            pk=version.pk, period_start__gt=day
        ).update(period_start=day)
        ScheduleVersion.objects.filter(
            pk=version.pk, period_end__lt=day
        ).update(period_end=day)

    def create(self, validated_data):
        schedule = super().create(validated_data)
        self._expand_version_period(schedule)
        return schedule

    def update(self, instance, validated_data):
        schedule = super().update(instance, validated_data)
        self._expand_version_period(schedule)
        return schedule


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
        # status/approved_* 只能經 approve/unapprove action 變更，防止 PATCH 繞過狀態機。
        # period_* 是後端自動維護的資料涵蓋範圍（無時間限制設計）：建立時以
        # 今日初始化，班次寫入時向外擴張，API 不接受直接輸入。
        read_only_fields = [
            'id', 'status', 'approved_by', 'approved_at',
            'period_start', 'period_end', 'created_at', 'updated_at'
        ]

    def create(self, validated_data):
        from django.utils import timezone
        today = timezone.localdate()
        validated_data.setdefault('period_start', today)
        validated_data.setdefault('period_end', today)
        return super().create(validated_data)


class ScheduleOverlapDecisionSerializer(serializers.ModelSerializer):
    decided_by_name = serializers.CharField(
        source='decided_by.username', read_only=True, default=''
    )

    class Meta:
        model = ScheduleOverlapDecision
        fields = [
            'id', 'conflict_key', 'organization', 'branch', 'version_type',
            'employee', 'schedule_date', 'schedule_ids', 'decision',
            'selected_schedule_ids', 'comment', 'decided_by', 'decided_by_name',
            'decided_at', 'created_at'
        ]
        # client 只送 conflict_key/schedule_ids/decision/selected/comment，
        # 其餘欄位由後端重算群組後自產（見 view）
        read_only_fields = [
            'id', 'organization', 'branch', 'version_type', 'employee',
            'schedule_date', 'decided_by', 'decided_at', 'created_at'
        ]


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
