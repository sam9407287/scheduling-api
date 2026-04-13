"""
AI Engine views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.conf import settings
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


def _load_schedule_data(schedule_version):
    """
    將 ScheduleVersion DB 物件轉為 AI provider 所需的 dict 格式。
    """
    from apps.schedules.models import Schedule
    from apps.shifts.models import ShiftTemplate
    from apps.employees.models import Employee

    schedules = (
        Schedule.objects
        .filter(schedule_version=schedule_version)
        .select_related('shift_template', 'employee')
    )

    assignments = []
    shift_ids = set()
    emp_ids = set()

    for s in schedules:
        assignments.append({
            'employee_id': s.employee_id,
            'date': s.schedule_date.isoformat(),
            'shift_id': s.shift_template_id,
            'shift_name': s.shift_template.name,
        })
        shift_ids.add(s.shift_template_id)
        emp_ids.add(s.employee_id)

    shift_templates = [
        {
            'id': st.id,
            'name': st.name,
            'start_time': st.start_time.strftime('%H:%M'),
            'end_time': st.end_time.strftime('%H:%M'),
            'break_minutes': st.break_minutes,
        }
        for st in ShiftTemplate.objects.filter(id__in=shift_ids)
    ]

    employees = [
        {
            'id': emp.id,
            'employee_id': emp.employee_id,
            'agreed_hours_per_week': float(emp.agreed_hours_per_week),
        }
        for emp in Employee.objects.filter(id__in=emp_ids)
    ]

    return {
        'assignments': assignments,
        'employees': employees,
        'shift_templates': shift_templates,
        'constraints': {
            'max_weekly_hours': 40,
            'min_rest_hours': 11,
            'max_consecutive_days': 6,
        },
    }


class AIEngineViewSet(viewsets.ViewSet):
    """AI 排班引擎 API"""
    permission_classes = [IsManager]

    @action(detail=False, methods=['post'])
    def generate(self, request):
        """
        產生排班表。

        request body:
          organization_id, branch_id?, period_start, period_end,
          employee_ids?, shift_template_ids?,
          constraints?, preferences?,
          run_async (bool, default false)
        """
        serializer = ScheduleRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        org_id = data['organization_id']
        branch_id = data.get('branch_id')
        period_start = data['period_start']
        period_end = data['period_end']

        # 取得員工
        from apps.employees.models import Employee
        from apps.schedules.models import Schedule as ScheduleModel

        employees_qs = Employee.objects.filter(
            organization_id=org_id, is_active=True
        ).prefetch_related('certifications')
        if branch_id:
            employees_qs = employees_qs.filter(branch_id=branch_id)
        if data.get('employee_ids'):
            employees_qs = employees_qs.filter(id__in=data['employee_ids'])

        # 建立員工不可用日期 map（來自其他已確認/完成版本的排班）
        employee_id_list = list(employees_qs.values_list('id', flat=True))
        unavailability_map: dict = {eid: [] for eid in employee_id_list}

        confirmed_schedules = (
            ScheduleModel.objects
            .filter(
                employee_id__in=employee_id_list,
                schedule_date__gte=period_start,
                schedule_date__lte=period_end,
                status__in=['confirmed', 'completed'],
            )
            .exclude(schedule_version__status='draft')
            .values('employee_id', 'schedule_date')
        )
        for s in confirmed_schedules:
            unavailability_map[s['employee_id']].append(s['schedule_date'].isoformat())

        # 合併呼叫方手動傳入的不可用日期
        manual_unavailability: dict = data.get('constraints', {}).get('employee_unavailability', {})

        employees = []
        for emp in employees_qs:
            # 可用性設定（blocked/preferred slots + required hours + special_rules）
            avail_data: dict = {}
            try:
                avail = emp.availability
                avail_data = {
                    'required_hours_per_week': (
                        float(avail.required_hours_per_week)
                        if avail.required_hours_per_week is not None else None
                    ),
                    'special_rules': avail.special_rules or '',
                    'blocked_slots': [
                        {
                            'day_of_week': s.day_of_week,
                            'start_time': s.start_time.strftime('%H:%M'),
                            'end_time': s.end_time.strftime('%H:%M'),
                        }
                        for s in avail.time_slots.filter(slot_type='blocked')
                    ],
                    'preferred_slots': [
                        {
                            'day_of_week': s.day_of_week,
                            'start_time': s.start_time.strftime('%H:%M'),
                            'end_time': s.end_time.strftime('%H:%M'),
                        }
                        for s in avail.time_slots.filter(slot_type='preferred')
                    ],
                }
            except Exception:
                pass  # 員工尚未設定可用性，視為無限制

            employees.append({
                'id': emp.id,
                'employee_id': emp.employee_id,
                'agreed_hours_per_week': float(emp.agreed_hours_per_week),
                'certifications': [c.id for c in emp.certifications.all()],
                'unavailable_dates': list(set(
                    unavailability_map.get(emp.id, [])
                    + manual_unavailability.get(str(emp.id), [])
                )),
                'availability': avail_data,
            })

        # 取得班別
        from apps.shifts.models import ShiftTemplate
        shifts_qs = ShiftTemplate.objects.filter(
            organization_id=org_id, is_active=True
        ).prefetch_related('required_certifications', 'employee_priorities')
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
                'employee_priorities': [
                    {
                        'employee_id': p.employee_id,
                        'priority_rank': p.priority_rank,
                        'max_extra_shifts': p.max_extra_shifts,
                    }
                    for p in shift.employee_priorities.all()
                ],
            }
            for shift in shifts_qs
        ]

        schedule_request = ScheduleRequest(
            organization_id=org_id,
            branch_id=branch_id,
            period_start=period_start,
            period_end=period_end,
            employees=employees,
            shift_templates=shifts,
            constraints=data.get('constraints', {}),
            preferences=data.get('preferences', {}),
        )

        if data.get('run_async', False):
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
            return Response(
                {'task_id': task.id, 'status': 'pending', 'message': '排班任務已提交，請稍後查詢結果'},
                status=status.HTTP_202_ACCEPTED,
            )

        provider = get_ai_provider()
        result = provider.generate_schedule(schedule_request)
        return Response(ScheduleResultSerializer(result).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def optimize(self, request):
        """
        優化現有排班版本。

        request body:
          schedule_version_id (int),
          constraints? (dict, 可覆蓋預設規則),
          run_async (bool, default false)
        """
        from apps.schedules.models import ScheduleVersion

        version_id = request.data.get('schedule_version_id')
        if not version_id:
            return Response(
                {'error': 'schedule_version_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            schedule_version = ScheduleVersion.objects.get(id=version_id)
        except ScheduleVersion.DoesNotExist:
            return Response({'error': 'Schedule version not found'}, status=status.HTTP_404_NOT_FOUND)

        if not request.user.is_superuser and schedule_version.organization != request.user.organization:
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

        current_schedule = _load_schedule_data(schedule_version)
        extra_constraints = request.data.get('constraints', {})
        constraints = {
            **current_schedule['constraints'],
            **extra_constraints,
            'period_start': schedule_version.period_start.isoformat(),
            'period_end': schedule_version.period_end.isoformat(),
            'organization_id': schedule_version.organization_id,
            'branch_id': schedule_version.branch_id,
        }

        provider = get_ai_provider()
        result = provider.optimize_schedule(current_schedule, constraints)
        return Response(ScheduleResultSerializer(result).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def check_compliance(self, request):
        """
        以 AI provider 檢查排班版本的勞基法合規性。

        request body: { schedule_version_id: int, constraints?: dict }
        """
        from apps.schedules.models import ScheduleVersion

        version_id = request.data.get('schedule_version_id')
        if not version_id:
            return Response(
                {'error': 'schedule_version_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            schedule_version = ScheduleVersion.objects.get(id=version_id)
        except ScheduleVersion.DoesNotExist:
            return Response({'error': 'Schedule version not found'}, status=status.HTTP_404_NOT_FOUND)

        if not request.user.is_superuser and schedule_version.organization != request.user.organization:
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

        schedule_data = _load_schedule_data(schedule_version)

        # 允許呼叫方覆蓋預設規則
        extra_constraints = request.data.get('constraints', {})
        if extra_constraints:
            schedule_data['constraints'].update(extra_constraints)

        provider = get_ai_provider()
        report = provider.check_compliance(schedule_data)

        return Response(
            {
                'is_compliant': report.is_compliant,
                'violations': report.violations,
                'warnings': report.warnings,
                'details': report.details,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'])
    def evaluate_change(self, request):
        """
        評估排班異動的合規影響。

        request body:
          schedule_version_id (int),
          proposed_change (dict):
            type: 'substitute' | 'cancel' | 'modify'
            employee_id: <db id>
            date: 'YYYY-MM-DD'
            shift_id: <班別 db id>
            new_employee_id: <db id>    (substitute 必填)
            new_shift_id: <db id>       (modify 選填)
            new_date: 'YYYY-MM-DD'      (modify 選填)
        """
        from apps.schedules.models import ScheduleVersion

        version_id = request.data.get('schedule_version_id')
        proposed_change = request.data.get('proposed_change')

        if not version_id or not proposed_change:
            return Response(
                {'error': 'schedule_version_id and proposed_change are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            schedule_version = ScheduleVersion.objects.get(id=version_id)
        except ScheduleVersion.DoesNotExist:
            return Response({'error': 'Schedule version not found'}, status=status.HTTP_404_NOT_FOUND)

        if not request.user.is_superuser and schedule_version.organization != request.user.organization:
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

        schedule_data = _load_schedule_data(schedule_version)

        provider = get_ai_provider()
        impact = provider.evaluate_change(schedule_data, proposed_change)

        return Response(
            {
                'can_apply': impact.can_apply,
                'impact_score': impact.impact_score,
                'violations': impact.violations,
                'warnings': impact.warnings,
                'affected_employees': impact.affected_employees,
            },
            status=status.HTTP_200_OK,
        )
