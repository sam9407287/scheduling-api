"""
AI Engine views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from django.utils import timezone
import importlib
from .serializers import ScheduleRequestSerializer, ScheduleResultSerializer
from .providers.base import BaseScheduleProvider, ScheduleRequest
from .tasks import generate_schedule_task
from apps.accounts.permissions import IsManager


def get_ai_provider() -> BaseScheduleProvider:
    """取得配置的 AI Provider 實例"""
    provider_path = settings.AI_SCHEDULE_PROVIDER
    module_path, class_name = provider_path.rsplit('.', 1)
    module = importlib.import_module(module_path)
    provider_class = getattr(module, class_name)
    return provider_class()


class AIEngineViewSet(viewsets.ViewSet):
    """AI 排班引擎 API"""
    permission_classes = [IsManager]
    
    @action(detail=False, methods=['post'])
    def generate(self, request):
        """
        產生排班表
        
        可以選擇同步或非同步執行
        """
        serializer = ScheduleRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        
        # 準備員工和班別資料
        from apps.employees.models import Employee
        from apps.shifts.models import ShiftTemplate
        
        org_id = data['organization_id']
        branch_id = data.get('branch_id')
        
        # 取得員工
        employees_qs = Employee.objects.filter(
            organization_id=org_id,
            is_active=True
        )
        if branch_id:
            employees_qs = employees_qs.filter(branch_id=branch_id)
        if data.get('employee_ids'):
            employees_qs = employees_qs.filter(id__in=data['employee_ids'])
        
        employees = [
            {
                'id': emp.id,
                'employee_id': emp.employee_id,
                'agreed_hours_per_week': float(emp.agreed_hours_per_week),
                'certifications': [c.id for c in emp.certifications.all()],
            }
            for emp in employees_qs
        ]
        
        # 取得班別
        shifts_qs = ShiftTemplate.objects.filter(
            organization_id=org_id,
            is_active=True
        )
        if data.get('shift_template_ids'):
            shifts_qs = shifts_qs.filter(id__in=data['shift_template_ids'])
        
        shifts = [
            {
                'id': shift.id,
                'name': shift.name,
                'start_time': shift.start_time.isoformat(),
                'end_time': shift.end_time.isoformat(),
                'break_minutes': shift.break_minutes,
                'min_staff_count': shift.min_staff_count,
                'required_certifications': [c.id for c in shift.required_certifications.all()],
            }
            for shift in shifts_qs
        ]
        
        # 建立 ScheduleRequest
        schedule_request = ScheduleRequest(
            organization_id=org_id,
            branch_id=branch_id,
            period_start=data['period_start'],
            period_end=data['period_end'],
            employees=employees,
            shift_templates=shifts,
            constraints=data.get('constraints', {}),
            preferences=data.get('preferences', {}),
        )
        
        # 選擇執行方式
        async_mode = request.data.get('async', False)
        
        if async_mode:
            # 非同步執行
            task = generate_schedule_task.delay({
                'organization_id': schedule_request.organization_id,
                'branch_id': schedule_request.branch_id,
                'period_start': schedule_request.period_start.isoformat(),
                'period_end': schedule_request.period_end.isoformat(),
                'employees': schedule_request.employees,
                'shift_templates': schedule_request.shift_templates,
                'constraints': schedule_request.constraints,
                'preferences': schedule_request.preferences,
            })
            
            return Response({
                'task_id': task.id,
                'status': 'pending',
                'message': '排班任務已提交，請稍後查詢結果'
            }, status=status.HTTP_202_ACCEPTED)
        else:
            # 同步執行
            provider = get_ai_provider()
            result = provider.generate_schedule(schedule_request)
            
            result_serializer = ScheduleResultSerializer(result)
            return Response(result_serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['post'])
    def check_compliance(self, request):
        """檢查排班表合規性"""
        # TODO: 實作合規檢查
        return Response({'message': '尚未實作'}, status=status.HTTP_501_NOT_IMPLEMENTED)
    
    @action(detail=False, methods=['post'])
    def evaluate_change(self, request):
        """評估排班異動影響"""
        # TODO: 實作影響評估
        return Response({'message': '尚未實作'}, status=status.HTTP_501_NOT_IMPLEMENTED)
