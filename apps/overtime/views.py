"""
Overtime views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum
from .models import OvertimeRecord, OvertimeRule
from .serializers import OvertimeRecordSerializer, OvertimeRuleSerializer
from apps.accounts.permissions import IsManager, IsSupervisor
from apps.attendance.models import Attendance


class OvertimeRuleViewSet(viewsets.ModelViewSet):
    """加班規則管理"""
    queryset = OvertimeRule.objects.select_related('organization')
    serializer_class = OvertimeRuleSerializer
    permission_classes = [IsManager]
    search_fields = ['organization__name']
    ordering_fields = ['organization', 'overtime_type']


class OvertimeRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """加班紀錄（唯讀，由系統自動計算）"""
    queryset = OvertimeRecord.objects.select_related('employee', 'attendance')
    serializer_class = OvertimeRecordSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ['employee__employee_id']
    ordering_fields = ['-overtime_date', 'employee']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee')
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(overtime_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(overtime_date__lte=date_to)
        
        return queryset
    
    @action(detail=False, methods=['post'])
    def calculate(self, request):
        """計算出勤記錄的加班時數"""
        attendance_id = request.data.get('attendance_id')
        if not attendance_id:
            return Response(
                {'error': 'attendance_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            attendance = Attendance.objects.get(id=attendance_id)
        except Attendance.DoesNotExist:
            return Response(
                {'error': 'Attendance not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # 計算加班
        overtime_records = self._calculate_overtime(attendance)
        
        serializer = OvertimeRecordSerializer(overtime_records, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    def _calculate_overtime(self, attendance: Attendance) -> list:
        """計算加班時數"""
        from datetime import datetime, time
        from decimal import Decimal
        
        if not attendance.actual_hours:
            return []
        
        # 取得對應的排班
        schedule = None
        try:
            schedule = attendance.employee.schedules.get(schedule_date=attendance.work_date)
        except:
            pass
        
        overtime_records = []
        
        if schedule:
            # 有排班，計算超出排班時數的部分
            expected_hours = float(schedule.expected_hours)
            actual_hours = float(attendance.actual_hours)
            
            if actual_hours > expected_hours:
                overtime_hours = actual_hours - expected_hours
                
                # 取得加班規則
                rule = OvertimeRule.objects.filter(
                    organization=attendance.employee.organization,
                    overtime_type='regular',
                    is_active=True
                ).first()
                
                multiplier = float(rule.multiplier) if rule else 1.34
                
                record = OvertimeRecord.objects.create(
                    employee=attendance.employee,
                    attendance=attendance,
                    overtime_date=attendance.work_date,
                    overtime_type='regular',
                    hours=Decimal(str(round(overtime_hours, 2))),
                    multiplier=Decimal(str(multiplier)),
                )
                overtime_records.append(record)
        else:
            # 無排班，可能是休息日或國定假日
            # TODO: 判斷是否為休息日/國定假日
            pass
        
        return overtime_records
