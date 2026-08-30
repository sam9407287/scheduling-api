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

from . import annual
from .models import LeaveRequest
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

        schedules = _affected_schedules(employee, start, end)
        return Response({
            'employee': employee.pk,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'affected_count': schedules.count(),
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
