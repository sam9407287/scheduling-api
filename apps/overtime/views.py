"""
Overtime views
"""
from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from django.db import transaction
from .models import OvertimeRecord, OvertimeRule
from .serializers import (
    OvertimeRecordSerializer, OvertimeRecordAdjustSerializer, OvertimeRuleSerializer,
)
from apps.accounts.permissions import IsManager, is_supervisor_or_above, scope_to_user_org
from apps.attendance.models import Attendance


class OvertimeRuleViewSet(viewsets.ModelViewSet):
    """加班規則管理"""
    queryset = OvertimeRule.objects.select_related('organization')
    serializer_class = OvertimeRuleSerializer
    permission_classes = [IsManager]
    search_fields = ['organization__name']
    ordering_fields = ['organization', 'overtime_type']

    def get_queryset(self):
        # 資料隔離：非超級使用者只看得到、改得到自己機構的規則
        return scope_to_user_org(super().get_queryset(), self.request.user)

    def _check_org(self, serializer):
        user = self.request.user
        organization = serializer.validated_data.get('organization')
        if (organization is not None and not user.is_superuser
                and organization.pk != user.organization_id):
            raise ValidationError({'organization': '只能設定自己機構的加班規則。'})

    def perform_create(self, serializer):
        self._check_org(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._check_org(serializer)
        serializer.save()


class OvertimeRecordViewSet(mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    """加班紀錄：由系統自動計算；管理者可 PATCH 倍率與備註（無 PUT、無 POST 建立）。"""
    queryset = OvertimeRecord.objects.select_related('employee', 'attendance')
    serializer_class = OvertimeRecordSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ['employee__employee_id']
    ordering_fields = ['-overtime_date', 'employee']
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_permissions(self):
        if self.action in ('update', 'partial_update'):
            return [IsManager()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action in ('update', 'partial_update'):
            return OvertimeRecordAdjustSerializer
        return super().get_serializer_class()

    def partial_update(self, request, *args, **kwargs):
        record = self.get_object()
        serializer = OvertimeRecordAdjustSerializer(record, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        record.refresh_from_db()
        return Response(OvertimeRecordSerializer(record).data)

    def get_queryset(self):
        # 資料隔離：非超級使用者只看得到自己機構的紀錄
        queryset = scope_to_user_org(
            super().get_queryset(), self.request.user, 'employee__organization')

        # 員工只能看自己的加班紀錄
        if not is_supervisor_or_above(self.request.user):
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
            queryset = queryset.filter(overtime_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(overtime_date__lte=date_to)

        return queryset
    
    @action(detail=False, methods=['post'])
    def calculate(self, request):
        """計算出勤記錄的加班時數。

        只能算自己機構的出勤（員工角色只能算自己的）；看不到的出勤一律 404。
        同一筆出勤已有加班紀錄時不重複產生，直接回既有紀錄（200）——管理者
        手動調過的倍率／備註因此不會被覆蓋。
        """
        attendance_id = request.data.get('attendance_id')
        if not attendance_id:
            return Response(
                {'error': 'attendance_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user
        visible = scope_to_user_org(Attendance.objects.all(), user, 'employee__organization')
        if not is_supervisor_or_above(user):
            visible = visible.filter(employee__user=user)

        with transaction.atomic():
            # 鎖住出勤列，兩個同時進來的請求不會各自產生一份
            try:
                attendance = visible.select_for_update().filter(pk=attendance_id).first()
            except (TypeError, ValueError):
                attendance = None
            if attendance is None:
                return Response(
                    {'error': 'Attendance not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            existing = list(self.queryset.filter(attendance=attendance))
            if existing:
                return Response(OvertimeRecordSerializer(existing, many=True).data)

            overtime_records = self._calculate_overtime(attendance)

        serializer = OvertimeRecordSerializer(overtime_records, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def _holiday_record(self, attendance: Attendance) -> OvertimeRecord:
        """國定假日出勤的加給紀錄，依員工薪制分流（PM 2026-10-13 補充）。

        - 時薪制：當日工資加倍（機構 holiday 規則倍率，預設 2.00）。
        - 月薪制：假日工資已含在月薪內，不加倍；以補休為原則，無法補休時
          才折發一日工資。先以 1.00 倍記錄並註明，由管理者決定後再調整。
        """
        from decimal import Decimal

        employee = attendance.employee
        rule = OvertimeRule.objects.filter(
            organization=employee.organization,
            overtime_type='holiday',
            is_active=True,
        ).first()
        if employee.pay_type == 'hourly':
            multiplier = rule.multiplier if rule else Decimal('2.00')
            notes = '時薪制國定假日出勤：工資加倍'
        else:
            multiplier = Decimal('1.00')
            notes = ('月薪制國定假日出勤：不加倍，以補休為原則；'
                     '無法補休時再折發一日工資（請手動調整倍率）')
        return OvertimeRecord.objects.create(
            employee=employee,
            attendance=attendance,
            overtime_date=attendance.work_date,
            overtime_type='holiday',
            hours=attendance.actual_hours.quantize(Decimal('0.01')),
            multiplier=multiplier,
            notes=notes,
        )

    def _calculate_overtime(self, attendance: Attendance) -> list:
        """計算加班時數"""
        from decimal import Decimal

        if not attendance.actual_hours:
            return []

        # 國定假日出勤（勞基法 §39）另走薪制分流，整日工時都算假日出勤
        from apps.compliance.models import Holiday
        if Holiday.objects.filter(
            organization=attendance.employee.organization,
            date=attendance.work_date,
        ).exists():
            return [self._holiday_record(attendance)]

        # 取得對應的排班（filter().first() 不會拋 DoesNotExist，不需 try/except）
        from apps.schedules.models import Schedule
        schedule = Schedule.objects.filter(
            employee=attendance.employee,
            schedule_date=attendance.work_date,
        ).first()

        overtime_records = []

        if schedule:
            # 使用 Decimal 計算，避免浮點精度誤差影響薪資
            actual_hours = attendance.actual_hours          # 已是 Decimal
            expected_hours = schedule.expected_hours        # 已是 Decimal

            if actual_hours > expected_hours:
                overtime_hours = actual_hours - expected_hours

                # 取得加班規則
                rule = OvertimeRule.objects.filter(
                    organization=attendance.employee.organization,
                    overtime_type='regular',
                    is_active=True
                ).first()

                multiplier = rule.multiplier if rule else Decimal('1.34')

                record = OvertimeRecord.objects.create(
                    employee=attendance.employee,
                    attendance=attendance,
                    overtime_date=attendance.work_date,
                    overtime_type='regular',
                    hours=overtime_hours.quantize(Decimal('0.01')),
                    multiplier=multiplier,
                )
                overtime_records.append(record)
        else:
            # 無排班出勤：依星期判斷加班類型
            # weekday() 5=週六, 6=週日 視為休息日；週一至週五無排班視為平日延伸
            if attendance.work_date.weekday() >= 5:
                overtime_type = 'rest_day'
                default_multiplier = Decimal('1.34')
            else:
                overtime_type = 'regular'
                default_multiplier = Decimal('1.34')

            rule = OvertimeRule.objects.filter(
                organization=attendance.employee.organization,
                overtime_type=overtime_type,
                is_active=True,
            ).first()
            multiplier = rule.multiplier if rule else default_multiplier

            record = OvertimeRecord.objects.create(
                employee=attendance.employee,
                attendance=attendance,
                overtime_date=attendance.work_date,
                overtime_type=overtime_type,
                hours=attendance.actual_hours.quantize(Decimal('0.01')),
                multiplier=multiplier,
            )
            overtime_records.append(record)

        return overtime_records
