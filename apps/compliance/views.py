"""
Compliance views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import LaborLawRule, ComplianceCheck
from .serializers import LaborLawRuleSerializer, ComplianceCheckSerializer
from .engine import ComplianceEngine
from apps.accounts.permissions import IsManager
from apps.schedules.models import ScheduleVersion


class LaborLawRuleViewSet(viewsets.ModelViewSet):
    """勞基法規則管理"""
    queryset = LaborLawRule.objects.all()
    serializer_class = LaborLawRuleSerializer
    permission_classes = [IsManager]
    search_fields = ['name']
    ordering_fields = ['rule_type', 'name']


class ComplianceCheckViewSet(viewsets.ReadOnlyModelViewSet):
    """合規檢查記錄（唯讀）"""
    queryset = ComplianceCheck.objects.select_related('organization', 'checked_by').all()
    serializer_class = ComplianceCheckSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ['organization__name']
    ordering_fields = ['-checked_at']
    
    def get_queryset(self):
        queryset = super().get_queryset()

        # 非管理員只能看自己機構的合規檢查
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(organization=self.request.user.organization)

        # Filter by organization
        org_id = self.request.query_params.get('organization')
        if org_id:
            queryset = queryset.filter(organization_id=org_id)

        # Filter by check_type
        check_type = self.request.query_params.get('check_type')
        if check_type:
            queryset = queryset.filter(check_type=check_type)

        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset
    
    @action(detail=False, methods=['post'])
    def check_schedule(self, request):
        """檢查排班表合規性"""
        schedule_version_id = request.data.get('schedule_version_id')
        if not schedule_version_id:
            return Response(
                {'error': 'schedule_version_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            schedule_version = ScheduleVersion.objects.get(id=schedule_version_id)
        except ScheduleVersion.DoesNotExist:
            return Response(
                {'error': 'Schedule version not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # 非 superuser 只能檢查自己機構的排班版本
        if not request.user.is_superuser:
            if schedule_version.organization != request.user.organization:
                return Response(
                    {'error': 'You do not have permission to check this organization\'s schedule'},
                    status=status.HTTP_403_FORBIDDEN
                )

        engine = ComplianceEngine()
        compliance_check = engine.check_schedule_compliance(schedule_version)

        serializer = ComplianceCheckSerializer(compliance_check)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def check_attendance(self, request):
        """檢查出勤合規性"""
        organization_id = request.data.get('organization_id')
        period_start = request.data.get('period_start')
        period_end = request.data.get('period_end')

        if not all([organization_id, period_start, period_end]):
            return Response(
                {'error': 'organization_id, period_start, and period_end are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 非 superuser 只能檢查自己機構
        if not request.user.is_superuser:
            if str(request.user.organization_id) != str(organization_id):
                return Response(
                    {'error': 'You do not have permission to check this organization\'s attendance'},
                    status=status.HTTP_403_FORBIDDEN
                )

        from datetime import datetime
        period_start = datetime.fromisoformat(period_start).date() if isinstance(period_start, str) else period_start
        period_end = datetime.fromisoformat(period_end).date() if isinstance(period_end, str) else period_end

        engine = ComplianceEngine()
        compliance_check = engine.check_attendance_compliance(
            organization_id, period_start, period_end
        )

        serializer = ComplianceCheckSerializer(compliance_check)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
