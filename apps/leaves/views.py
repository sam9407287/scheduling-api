"""
Leave management views.

Flow: employee submits (pending) -> supervisor+ approves/rejects.
Supervisor+ creating on behalf of an employee is auto-approved (phone-in
leave entered by the manager). Approval marks the employee's schedules in
range as status='leave' (kept, never deleted) and stores a snapshot so a
later cancellation restores them. Approved leave also feeds the AI solver
as hard unavailable dates (see apps.leaves.solver_dates).
"""
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsSupervisor
from apps.employees.models import Employee
from apps.schedules.models import Schedule
from apps.schedules.serializers import ScheduleSerializer

from . import annual, quotas
from .models import LeaveRequest, LeaveTypeQuota, OrgLeaveSettings
from .serializers import LeaveRequestSerializer


def _is_supervisor(user):
    if user.is_superuser:
        return True
    return bool(user.role and user.role.name in ('admin', 'manager', 'supervisor'))


def _own_employee(user):
    return Employee.objects.filter(user=user).first()


def _affected_schedules(employee, start_date, end_date):
    """該員工在起訖範圍內、非封存版本的班次（請假影響範圍）。"""
    return Schedule.objects.filter(
        employee=employee,
        schedule_date__gte=start_date,
        schedule_date__lte=end_date,
    ).exclude(
        schedule_version__status='archived'
    ).exclude(
        status__in=('cancelled', 'leave')
    ).select_related('shift_template', 'schedule_version')


class LeaveRequestViewSet(viewsets.ModelViewSet):
    """請假申請：員工看自己的、主管看全機構。"""
    queryset = LeaveRequest.objects.select_related(
        'employee', 'employee__user', 'created_by', 'reviewed_by')
    serializer_class = LeaveRequestSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']  # 修改一律走 action

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if not user.is_superuser:
            if user.organization:
                queryset = queryset.filter(organization=user.organization)
            else:
                return queryset.none()
        if not _is_supervisor(user):
            queryset = queryset.filter(employee__user=user)

        params = self.request.query_params
        if params.get('status'):
            queryset = queryset.filter(status=params['status'])
        if params.get('employee'):
            queryset = queryset.filter(employee_id=params['employee'])
        if params.get('date_from'):
            queryset = queryset.filter(end_date__gte=params['date_from'])
        if params.get('date_to'):
            queryset = queryset.filter(start_date__lte=params['date_to'])
        return queryset

    def create(self, request, *args, **kwargs):
        user = request.user
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee = serializer.validated_data['employee']

        own = _own_employee(user)
        on_behalf = own is None or employee.pk != own.pk
        if on_behalf and not _is_supervisor(user):
            return Response(
                {'error': 'You can only submit leave requests for yourself.'},
                status=status.HTTP_403_FORBIDDEN
            )
        if not user.is_superuser and employee.organization_id != user.organization_id:
            return Response(
                {'error': 'Employee must belong to your organization.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 本人申請（含主管幫自己請假）一律 pending 送審；
        # 只有「主管替其他員工」代登記才自動核准（LEAVE_V2 P0）。
        source = 'manager_proxy' if on_behalf else 'self'
        leave = serializer.save(
            organization=employee.organization,
            created_by=user,
            submission_source=source,
        )

        if on_behalf:
            self._apply_approval(leave, user, note='主管代登記，自動核准')
            leave.refresh_from_db()

        return Response(self.get_serializer(leave).data, status=status.HTTP_201_CREATED)

    def _apply_approval(self, leave, reviewer, note=''):
        """pending → approved（原子），並把範圍內班次標為請假。"""
        updated = LeaveRequest.objects.filter(
            pk=leave.pk, status='pending'
        ).update(
            status='approved',
            reviewed_by=reviewer,
            reviewed_at=timezone.now(),
            review_note=note,
        )
        if not updated:
            return False
        # time_range 部分請假不動班次狀態——純疊顯示，班次照舊
        if leave.request_unit == 'time_range':
            LeaveRequest.objects.filter(pk=leave.pk).update(affected_schedule_ids=[])
            return True
        affected = list(_affected_schedules(leave.employee, leave.start_date, leave.end_date))
        snapshot = [{'id': s.pk, 'prev_status': s.status} for s in affected]
        Schedule.objects.filter(pk__in=[s.pk for s in affected]).update(status='leave')
        LeaveRequest.objects.filter(pk=leave.pk).update(affected_schedule_ids=snapshot)
        return True

    @staticmethod
    def _is_own_request(leave, user):
        return leave.employee.user_id == user.pk

    @action(detail=True, methods=['post'], permission_classes=[IsSupervisor])
    def approve(self, request, pk=None):
        leave = self.get_object()
        if self._is_own_request(leave, request.user):
            return Response(
                {'code': 'self_approval_forbidden',
                 'error': 'You cannot approve your own leave request.'},
                status=status.HTTP_403_FORBIDDEN
            )
        if not self._apply_approval(leave, request.user,
                                    note=(request.data.get('note') or '').strip()):
            return Response(
                {'code': 'leave_not_pending', 'error': 'Only pending requests can be approved.'},
                status=status.HTTP_409_CONFLICT
            )
        leave.refresh_from_db()
        return Response(self.get_serializer(leave).data)

    @action(detail=True, methods=['post'], permission_classes=[IsSupervisor])
    def reject(self, request, pk=None):
        leave = self.get_object()
        if self._is_own_request(leave, request.user):
            return Response(
                {'code': 'self_approval_forbidden',
                 'error': 'You cannot review your own leave request; cancel it instead.'},
                status=status.HTTP_403_FORBIDDEN
            )
        note = (request.data.get('note') or '').strip()
        if not note:
            return Response({'error': 'note is required when rejecting'},
                            status=status.HTTP_400_BAD_REQUEST)
        updated = LeaveRequest.objects.filter(pk=leave.pk, status='pending').update(
            status='rejected', reviewed_by=request.user,
            reviewed_at=timezone.now(), review_note=note,
        )
        if not updated:
            return Response(
                {'code': 'leave_not_pending', 'error': 'Only pending requests can be rejected.'},
                status=status.HTTP_409_CONFLICT
            )
        leave.refresh_from_db()
        return Response(self.get_serializer(leave).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """員工取消自己的 pending；主管可取消 approved（還原班次狀態）。"""
        leave = self.get_object()
        user = request.user
        own = _own_employee(user)
        is_own = own is not None and leave.employee_id == own.pk

        if leave.status == 'pending':
            if not (is_own or _is_supervisor(user)):
                return Response(status=status.HTTP_403_FORBIDDEN)
            updated = LeaveRequest.objects.filter(pk=leave.pk, status='pending').update(
                status='cancelled', reviewed_by=user, reviewed_at=timezone.now())
        elif leave.status == 'approved':
            if not _is_supervisor(user):
                return Response(
                    {'error': 'Only supervisors can cancel an approved leave.'},
                    status=status.HTTP_403_FORBIDDEN
                )
            updated = LeaveRequest.objects.filter(pk=leave.pk, status='approved').update(
                status='cancelled', reviewed_by=user, reviewed_at=timezone.now())
            if updated:
                # 依核准當下的快照還原班次狀態（其後被手動改過的格子不動）
                for item in leave.affected_schedule_ids:
                    Schedule.objects.filter(
                        pk=item['id'], status='leave'
                    ).update(status=item['prev_status'])
        else:
            updated = 0

        if not updated:
            return Response(
                {'code': 'leave_not_cancellable',
                 'error': 'Only pending or approved requests can be cancelled.'},
                status=status.HTTP_409_CONFLICT
            )
        leave.refresh_from_db()
        return Response(self.get_serializer(leave).data)

    @action(detail=False, methods=['get'])
    def impact(self, request):
        """送出前/審核時的影響預覽：該員工該區間的既有班次。"""
        employee_id = request.query_params.get('employee')
        start = parse_date(request.query_params.get('start_date') or '')
        end = parse_date(request.query_params.get('end_date') or '')
        if not employee_id or not start or not end or end < start:
            return Response(
                {'error': 'employee, start_date and end_date are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        employees = Employee.objects.all()
        if not request.user.is_superuser:
            employees = employees.filter(organization=request.user.organization)
        employee = employees.filter(pk=employee_id).first()
        if employee is None:
            return Response({'error': 'employee not found'}, status=status.HTTP_404_NOT_FOUND)
        if not _is_supervisor(request.user):
            own = _own_employee(request.user)
            if own is None or own.pk != employee.pk:
                return Response(status=status.HTTP_403_FORBIDDEN)

        schedules = list(_affected_schedules(employee, start, end))

        # 時段假（可選）：計算與班次的重疊分鐘數與每日拆解
        leave_start = parse_date(request.query_params.get('start_date') or '')
        start_time_raw = request.query_params.get('start_time')
        end_time_raw = request.query_params.get('end_time')
        overlap_minutes = None
        daily = {}
        for row in schedules:
            template = row.shift_template
            shift_minutes = int(float(template.duration_hours) * 60)
            day_key = row.schedule_date.isoformat()
            daily.setdefault(day_key, {'date': day_key, 'scheduled_minutes': 0, 'leave_minutes': 0})
            daily[day_key]['scheduled_minutes'] += shift_minutes
            if start_time_raw and end_time_raw:
                overlap = _overlap_minutes(
                    template.start_time, template.end_time, start_time_raw, end_time_raw)
                daily[day_key]['leave_minutes'] += overlap
            else:
                daily[day_key]['leave_minutes'] += shift_minutes
        if start_time_raw and end_time_raw:
            overlap_minutes = sum(d['leave_minutes'] for d in daily.values())

        return Response({
            'employee': employee.pk,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'affected_count': len(schedules),
            'overlap_minutes': overlap_minutes,
            'daily_breakdown': sorted(daily.values(), key=lambda d: d['date']),
            'schedules': ScheduleSerializer(schedules, many=True).data,
        })

    @action(detail=False, methods=['get'])
    def balance(self, request):
        """特休餘額：entitled / used / remaining（依勞基法年資級距）。"""
        employee_id = request.query_params.get('employee')
        user = request.user
        if employee_id:
            employees = Employee.objects.all()
            if not user.is_superuser:
                employees = employees.filter(organization=user.organization)
            employee = employees.filter(pk=employee_id).first()
            if employee is None:
                return Response({'error': 'employee not found'}, status=status.HTTP_404_NOT_FOUND)
            if not _is_supervisor(user):
                own = _own_employee(user)
                if own is None or own.pk != employee.pk:
                    return Response(status=status.HTTP_403_FORBIDDEN)
        else:
            employee = _own_employee(user)
            if employee is None:
                return Response({'error': 'employee is required'}, status=status.HTTP_400_BAD_REQUEST)

        today = timezone.localdate()
        entitled = annual.entitled_days(employee.hire_date, today)
        window_start, window_end = annual.entitlement_year(employee.hire_date, today)

        used = 0
        if window_start:
            for leave in LeaveRequest.objects.filter(
                employee=employee, leave_type='annual', status='approved',
                end_date__gte=window_start, start_date__lte=window_end,
            ):
                overlap_start = max(leave.start_date, window_start)
                overlap_end = min(leave.end_date, window_end)
                used += (overlap_end - overlap_start).days + 1

        return Response({
            'employee': employee.pk,
            'hire_date': employee.hire_date.isoformat(),
            'as_of': today.isoformat(),
            'entitlement_year_start': window_start.isoformat() if window_start else None,
            'entitlement_year_end': window_end.isoformat() if window_end else None,
            'entitled_days': entitled,
            'used_days': used,
            'remaining_days': max(0, entitled - used),
        })


def _overlap_minutes(shift_start, shift_end, leave_start_str, leave_end_str):
    """班次與請假時段的重疊分鐘數（班次跨午夜視為延伸到隔日）。"""
    def to_min(t):
        if isinstance(t, str):
            parts = t.split(':')
            return int(parts[0]) * 60 + int(parts[1])
        return t.hour * 60 + t.minute

    s1, e1 = to_min(shift_start), to_min(shift_end)
    s2, e2 = to_min(leave_start_str), to_min(leave_end_str)
    if e1 <= s1:
        e1 += 1440  # 跨午夜班
    return max(0, min(e1, e2) - max(s1, s2))


class LeaveSettingsView(viewsets.ViewSet):
    """機構請假設定：day_minutes ＋ 各假別額度（GET / PUT 整批）。"""
    permission_classes = [IsSupervisor]

    def _org(self, request):
        if request.user.is_superuser and request.query_params.get('organization'):
            from apps.organizations.models import Organization
            return Organization.objects.filter(
                pk=request.query_params['organization']).first()
        return request.user.organization

    def list(self, request):
        org = self._org(request)
        if org is None:
            return Response({'error': 'organization is required'}, status=status.HTTP_400_BAD_REQUEST)
        day_minutes = quotas.day_minutes_for(org.pk)
        quota_map = quotas.resolve_quota_minutes(org.pk, day_minutes)
        configured = {
            q.leave_type: q for q in LeaveTypeQuota.objects.filter(organization=org)
        }
        return Response({
            'organization': org.pk,
            'day_minutes': day_minutes,
            'quotas': [
                {
                    'leave_type': lt,
                    'leave_type_display': display,
                    'annual_quota_minutes': quota_map.get(lt),
                    'is_default': lt not in configured,
                }
                for lt, display in LeaveRequest.LEAVE_TYPE_CHOICES
                if lt != 'annual'  # 特休永遠法定級距，不在此設定
            ],
        })

    def put(self, request):
        org = self._org(request)
        if org is None:
            return Response({'error': 'organization is required'}, status=status.HTTP_400_BAD_REQUEST)

        day_minutes = request.data.get('day_minutes')
        if day_minutes is not None:
            try:
                day_minutes = int(day_minutes)
                if not (60 <= day_minutes <= 1440):
                    raise ValueError
            except (TypeError, ValueError):
                return Response({'error': 'day_minutes must be an integer between 60 and 1440'},
                                status=status.HTTP_400_BAD_REQUEST)
            OrgLeaveSettings.objects.update_or_create(
                organization=org, defaults={'day_minutes': day_minutes})

        valid_types = {lt for lt, _ in LeaveRequest.LEAVE_TYPE_CHOICES} - {'annual'}
        for item in request.data.get('quotas') or []:
            leave_type = item.get('leave_type')
            if leave_type not in valid_types:
                return Response({'error': f'invalid leave_type: {leave_type}'},
                                status=status.HTTP_400_BAD_REQUEST)
            LeaveTypeQuota.objects.update_or_create(
                organization=org, leave_type=leave_type,
                defaults={
                    'annual_quota_minutes': item.get('annual_quota_minutes'),
                    'is_active': item.get('is_active', True),
                },
            )
        return self.list(request)


class LeaveBalancesView(viewsets.ViewSet):
    """多假別餘額面板（分鐘制）。GET /api/leaves/balances/?employee=N"""
    permission_classes = [IsAuthenticated]

    def list(self, request):
        employee_id = request.query_params.get('employee')
        user = request.user
        if employee_id:
            employees = Employee.objects.all()
            if not user.is_superuser:
                employees = employees.filter(organization=user.organization)
            employee = employees.filter(pk=employee_id).first()
            if employee is None:
                return Response({'error': 'employee not found'}, status=status.HTTP_404_NOT_FOUND)
            if not _is_supervisor(user):
                own = _own_employee(user)
                if own is None or own.pk != employee.pk:
                    return Response(status=status.HTTP_403_FORBIDDEN)
        else:
            employee = _own_employee(user)
            if employee is None:
                return Response({'error': 'employee is required'}, status=status.HTTP_400_BAD_REQUEST)

        today = timezone.localdate()
        day_minutes = quotas.day_minutes_for(employee.organization_id)
        window_start, window_end = annual.entitlement_year(employee.hire_date, today)

        # 特休 entitled 走法定級距；其他假別走 quota 解析
        quota_map = quotas.resolve_quota_minutes(employee.organization_id, day_minutes)
        annual_entitled = annual.entitled_days(employee.hire_date, today) * day_minutes

        used = {lt: 0 for lt, _ in LeaveRequest.LEAVE_TYPE_CHOICES}
        pending = {lt: 0 for lt, _ in LeaveRequest.LEAVE_TYPE_CHOICES}
        if window_start:
            rows = LeaveRequest.objects.filter(
                employee=employee, status__in=('approved', 'pending'),
                end_date__gte=window_start, start_date__lte=window_end,
            )
            for leave in rows:
                minutes = leave.duration_minutes(day_minutes)
                bucket = used if leave.status == 'approved' else pending
                bucket[leave.leave_type] += minutes

        balances = []
        for leave_type, display in LeaveRequest.LEAVE_TYPE_CHOICES:
            entitled = annual_entitled if leave_type == 'annual' else quota_map.get(leave_type)
            remaining = (entitled - used[leave_type]) if entitled is not None else None
            balances.append({
                'leave_type': leave_type,
                'leave_type_display': display,
                'entitled_minutes': entitled,
                'used_minutes': used[leave_type],
                'pending_minutes': pending[leave_type],
                'remaining_minutes': remaining,  # 可為負值：超額警告不擋
            })

        return Response({
            'employee': employee.pk,
            'as_of': today.isoformat(),
            'day_minutes': day_minutes,
            'entitlement_year_start': window_start.isoformat() if window_start else None,
            'entitlement_year_end': window_end.isoformat() if window_end else None,
            'balances': balances,
        })
