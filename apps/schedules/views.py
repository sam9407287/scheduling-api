"""
Schedule views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import Schedule, ScheduleVersion, ScheduleChange
from .serializers import (
    ScheduleSerializer,
    ScheduleVersionSerializer,
    ScheduleChangeSerializer
)
from apps.accounts.permissions import IsManager, IsSupervisor


class ScheduleVersionViewSet(viewsets.ModelViewSet):
    """排班版本管理"""
    queryset = ScheduleVersion.objects.select_related('organization', 'branch', 'approved_by', 'created_by').prefetch_related('schedules')
    serializer_class = ScheduleVersionSerializer
    permission_classes = [IsSupervisor]
    search_fields = ['version_label', 'organization__name']
    ordering_fields = ['-period_start', '-created_at']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by organization
        org_id = self.request.query_params.get('organization')
        if org_id:
            queryset = queryset.filter(organization_id=org_id)
        
        # Filter by version_type
        version_type = self.request.query_params.get('version_type')
        if version_type:
            queryset = queryset.filter(version_type=version_type)
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """簽核排班版本"""
        from django.utils import timezone

        version = self.get_object()

        # 使用原子性 update，只在 status=draft 時才更新，避免並發重複簽核
        updated = ScheduleVersion.objects.filter(
            pk=version.pk,
            status='draft'
        ).update(
            status='approved',
            approved_by=request.user,
            approved_at=timezone.now()
        )

        if not updated:
            return Response(
                {'error': 'Only draft versions can be approved, or it was already approved.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        version.refresh_from_db()
        serializer = self.get_serializer(version)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def create_dual_versions(self, request, pk=None):
        """建立雙軌版本（法規版和實際版）"""
        legal_version = self.get_object()
        
        # 建立實際版
        actual_version = ScheduleVersion.objects.create(
            organization=legal_version.organization,
            branch=legal_version.branch,
            version_label=f"{legal_version.version_label} (實際版)",
            version_type='actual',
            period_start=legal_version.period_start,
            period_end=legal_version.period_end,
            status='draft',
            created_by=request.user,
        )
        
        # 複製排班到實際版
        for schedule in legal_version.schedules.all():
            Schedule.objects.create(
                schedule_version=actual_version,
                employee=schedule.employee,
                shift_template=schedule.shift_template,
                schedule_date=schedule.schedule_date,
                expected_hours=schedule.expected_hours,
                status=schedule.status,
                notes=schedule.notes,
            )
        
        serializer = ScheduleVersionSerializer(actual_version)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get'])
    def compare(self, request, pk=None):
        """比對兩個版本的差異"""
        version1 = self.get_object()
        version2_id = request.query_params.get('version2_id')
        
        if not version2_id:
            return Response(
                {'error': 'version2_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            version2 = ScheduleVersion.objects.get(id=version2_id)
        except ScheduleVersion.DoesNotExist:
            return Response(
                {'error': 'Version 2 not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # 比對差異
        schedules1 = {f"{s.employee_id}_{s.schedule_date}_{s.shift_template_id}": s for s in version1.schedules.all()}
        schedules2 = {f"{s.employee_id}_{s.schedule_date}_{s.shift_template_id}": s for s in version2.schedules.all()}
        
        only_in_v1 = [str(k) for k in schedules1.keys() if k not in schedules2]
        only_in_v2 = [str(k) for k in schedules2.keys() if k not in schedules1]
        differences = []
        
        for key in schedules1.keys() & schedules2.keys():
            s1 = schedules1[key]
            s2 = schedules2[key]
            # key 已包含 employee_id / schedule_date / shift_template_id，
            # 故只需比對可能變動的欄位：expected_hours、status、notes
            if (s1.expected_hours != s2.expected_hours
                    or s1.status != s2.status
                    or s1.notes != s2.notes):
                differences.append({
                    'key': key,
                    'version1': ScheduleSerializer(s1).data,
                    'version2': ScheduleSerializer(s2).data,
                })
        
        return Response({
            'version1': ScheduleVersionSerializer(version1).data,
            'version2': ScheduleVersionSerializer(version2).data,
            'only_in_version1': only_in_v1,
            'only_in_version2': only_in_v2,
            'differences': differences,
        })


class ScheduleViewSet(viewsets.ModelViewSet):
    """排班管理"""
    queryset = Schedule.objects.select_related('employee', 'shift_template', 'schedule_version')
    serializer_class = ScheduleSerializer
    permission_classes = [IsSupervisor]
    search_fields = ['employee__employee_id', 'employee__user__username']
    ordering_fields = ['schedule_date', 'created_at']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by schedule_version
        version_id = self.request.query_params.get('version')
        if version_id:
            queryset = queryset.filter(schedule_version_id=version_id)
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee')
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(schedule_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(schedule_date__lte=date_to)
        
        return queryset


class ScheduleChangeViewSet(viewsets.ModelViewSet):
    """排班異動管理"""
    queryset = ScheduleChange.objects.select_related('schedule', 'original_employee', 'replacement_employee', 'changed_by', 'approved_by')
    serializer_class = ScheduleChangeSerializer
    permission_classes = [IsSupervisor]
    search_fields = ['schedule__employee__employee_id', 'reason']
    ordering_fields = ['-changed_at']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by schedule
        schedule_id = self.request.query_params.get('schedule')
        if schedule_id:
            queryset = queryset.filter(schedule_id=schedule_id)
        
        # Filter by change_type
        change_type = self.request.query_params.get('change_type')
        if change_type:
            queryset = queryset.filter(change_type=change_type)
        
        return queryset
