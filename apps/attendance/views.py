"""
Attendance views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from decimal import Decimal
from .models import Attendance, AnomalyRecord
from .serializers import AttendanceSerializer, AnomalyRecordSerializer
from apps.accounts.permissions import IsEmployeeOrAbove, IsSupervisor
from apps.employees.models import Employee


class AttendanceViewSet(viewsets.ModelViewSet):
    """出勤管理"""
    queryset = Attendance.objects.select_related('employee', 'substitute_for', 'cross_branch')
    serializer_class = AttendanceSerializer
    permission_classes = [IsEmployeeOrAbove]
    search_fields = ['employee__employee_id', 'employee__user__username']
    ordering_fields = ['-work_date', '-created_at']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # 員工只能看自己的出勤
        if not (self.request.user.is_superuser or 
                (self.request.user.role and self.request.user.role.name in ['admin', 'manager', 'supervisor'])):
            try:
                employee = self.request.user.employee_profile
                queryset = queryset.filter(employee=employee)
            except AttributeError:
                queryset = queryset.none()
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee')
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(work_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(work_date__lte=date_to)
        
        # Filter by anomaly
        anomaly = self.request.query_params.get('anomaly')
        if anomaly is not None:
            queryset = queryset.filter(anomaly_flag=anomaly.lower() == 'true')
        
        return queryset
    
    @action(detail=False, methods=['post'])
    def clock_in(self, request):
        """上班打卡"""
        try:
            employee = request.user.employee_profile
        except (AttributeError, Employee.DoesNotExist):
            return Response(
                {'error': 'Employee profile not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        today = timezone.now().date()
        
        # 檢查是否已打卡
        attendance, created = Attendance.objects.get_or_create(
            employee=employee,
            work_date=today,
            defaults={'clock_in': timezone.now()}
        )
        
        if not created:
            if attendance.clock_in:
                return Response(
                    {'error': 'Already clocked in today'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            attendance.clock_in = timezone.now()
            attendance.save()
        
        serializer = AttendanceSerializer(attendance)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def clock_out(self, request):
        """下班打卡"""
        try:
            employee = request.user.employee_profile
        except (AttributeError, Employee.DoesNotExist):
            return Response(
                {'error': 'Employee profile not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        today = timezone.now().date()
        
        try:
            attendance = Attendance.objects.get(employee=employee, work_date=today)
        except Attendance.DoesNotExist:
            return Response(
                {'error': 'No clock-in record found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        if attendance.clock_out:
            return Response(
                {'error': 'Already clocked out today'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        attendance.clock_out = timezone.now()
        attendance.calculate_hours()
        
        # 檢查異常
        self._check_anomalies(attendance)
        
        attendance.save()
        serializer = AttendanceSerializer(attendance)
        return Response(serializer.data)
    
    def _check_anomalies(self, attendance):
        """檢查出勤異常：超時、遲到、早退、無排班出勤"""
        from datetime import datetime, timedelta
        from apps.schedules.models import Schedule
        from django.utils import timezone as tz_utils

        if not attendance.clock_in or not attendance.clock_out:
            return

        # 將打卡時間轉為本地時間（naive）以便與班別 TimeField 比較
        local_in = tz_utils.localtime(attendance.clock_in).replace(tzinfo=None)
        local_out = tz_utils.localtime(attendance.clock_out).replace(tzinfo=None)

        anomaly_reasons = []
        GRACE_MINUTES = 5  # 5 分鐘寬容

        # --- 超時工作（超過 12 小時）---
        if attendance.actual_hours and attendance.actual_hours > Decimal('12'):
            AnomalyRecord.objects.create(
                attendance=attendance,
                anomaly_type='overtime',
                description=f'工作時數 {attendance.actual_hours} 小時，超過 12 小時',
                severity='high',
            )
            anomaly_reasons.append('超時工作')

        # 查詢當日排班
        schedule = (
            Schedule.objects
            .filter(employee=attendance.employee, schedule_date=attendance.work_date)
            .select_related('shift_template')
            .first()
        )

        if schedule:
            shift = schedule.shift_template

            # --- 遲到 ---
            scheduled_start = datetime.combine(attendance.work_date, shift.start_time)
            late_threshold = scheduled_start + timedelta(minutes=GRACE_MINUTES)
            if local_in > late_threshold:
                late_minutes = int((local_in - scheduled_start).total_seconds() / 60)
                severity = 'low' if late_minutes <= 15 else ('medium' if late_minutes <= 60 else 'high')
                AnomalyRecord.objects.create(
                    attendance=attendance,
                    anomaly_type='late',
                    description=(
                        f'遲到 {late_minutes} 分鐘'
                        f'（排班開始 {shift.start_time.strftime("%H:%M")}，'
                        f'實際打卡 {local_in.strftime("%H:%M")}）'
                    ),
                    severity=severity,
                )
                anomaly_reasons.append('遲到')

            # --- 早退 ---
            # 夜班（end_time < start_time）結束時間在隔天
            if shift.end_time > shift.start_time:
                scheduled_end = datetime.combine(attendance.work_date, shift.end_time)
            else:
                scheduled_end = datetime.combine(
                    attendance.work_date + timedelta(days=1), shift.end_time
                )
            early_threshold = scheduled_end - timedelta(minutes=GRACE_MINUTES)
            if local_out < early_threshold:
                early_minutes = int((scheduled_end - local_out).total_seconds() / 60)
                severity = 'low' if early_minutes <= 15 else ('medium' if early_minutes <= 60 else 'high')
                AnomalyRecord.objects.create(
                    attendance=attendance,
                    anomaly_type='early_leave',
                    description=(
                        f'早退 {early_minutes} 分鐘'
                        f'（排班結束 {shift.end_time.strftime("%H:%M")}，'
                        f'實際打卡 {local_out.strftime("%H:%M")}）'
                    ),
                    severity=severity,
                )
                anomaly_reasons.append('早退')

        else:
            # 無排班卻出勤 → mismatch
            AnomalyRecord.objects.create(
                attendance=attendance,
                anomaly_type='mismatch',
                description=f'員工 {attendance.employee.employee_id} 於 {attendance.work_date} 無排班卻出勤',
                severity='medium',
            )
            anomaly_reasons.append('無排班出勤')

        if anomaly_reasons:
            attendance.anomaly_flag = True
            attendance.anomaly_reason = '；'.join(anomaly_reasons)


class AnomalyRecordViewSet(viewsets.ModelViewSet):
    """異常紀錄管理"""
    queryset = AnomalyRecord.objects.select_related('attendance', 'resolved_by')
    serializer_class = AnomalyRecordSerializer
    permission_classes = [IsSupervisor]
    search_fields = ['attendance__employee__employee_id', 'description']
    ordering_fields = ['-created_at', 'severity']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by resolved
        resolved = self.request.query_params.get('resolved')
        if resolved is not None:
            queryset = queryset.filter(resolved=resolved.lower() == 'true')
        
        # Filter by severity
        severity = self.request.query_params.get('severity')
        if severity:
            queryset = queryset.filter(severity=severity)
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """處理異常"""
        anomaly = self.get_object()
        anomaly.resolved = True
        anomaly.resolved_by = request.user
        anomaly.resolved_at = timezone.now()
        anomaly.resolution_notes = request.data.get('notes', '')
        anomaly.save()
        
        serializer = self.get_serializer(anomaly)
        return Response(serializer.data)
