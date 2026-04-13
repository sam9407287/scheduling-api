"""
Labor Law Compliance Engine
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any
from django.db.models import Sum, Q
from apps.employees.models import Employee
from apps.schedules.models import Schedule, ScheduleVersion
from apps.attendance.models import Attendance
from .models import LaborLawRule, ComplianceCheck


class ComplianceEngine:
    """勞基法合規檢查引擎"""
    
    # 預設勞基法規則
    DEFAULT_RULES = {
        'max_weekly_hours': 40,  # 每週正常工時
        'max_daily_hours': 8,  # 每日最大工時
        'min_rest_hours': 11,  # 兩班之間最小休息時數
        'max_consecutive_days': 6,  # 最大連續工作天數
        'mandatory_rest_day': 1,  # 每週至少休息日數
    }
    
    def check_schedule_compliance(
        self,
        schedule_version: ScheduleVersion,
        rules: Dict[str, Any] = None
    ) -> ComplianceCheck:
        """
        檢查排班表合規性
        
        Args:
            schedule_version: 排班版本
            rules: 自訂規則（覆蓋預設規則）
            
        Returns:
            ComplianceCheck: 合規檢查結果
        """
        if rules is None:
            rules = self.DEFAULT_RULES.copy()
        
        violations = []
        warnings = []
        
        # 取得所有排班
        schedules = Schedule.objects.filter(schedule_version=schedule_version)
        
        # 按員工分組檢查
        employees = Employee.objects.filter(
            schedules__schedule_version=schedule_version
        ).distinct()
        
        for employee in employees:
            emp_schedules = schedules.filter(employee=employee).order_by('schedule_date')
            
            # 檢查每週工時
            weekly_violations = self._check_weekly_hours(
                emp_schedules, employee, rules.get('max_weekly_hours', 40)
            )
            violations.extend(weekly_violations)
            
            # 檢查連續工作天數
            consecutive_violations = self._check_consecutive_days(
                emp_schedules, rules.get('max_consecutive_days', 6)
            )
            violations.extend(consecutive_violations)
            
            # 檢查休息間隔
            rest_violations = self._check_rest_interval(
                emp_schedules, rules.get('min_rest_hours', 11)
            )
            violations.extend(rest_violations)
        
        # 建立合規檢查記錄
        status = 'pass' if len(violations) == 0 else ('warning' if len(warnings) > 0 else 'violation')
        
        compliance_check = ComplianceCheck.objects.create(
            organization=schedule_version.organization,
            check_type='schedule',
            check_period_start=schedule_version.period_start,
            check_period_end=schedule_version.period_end,
            status=status,
            violations=violations,
            warnings=warnings,
        )
        
        return compliance_check
    
    def check_attendance_compliance(
        self,
        organization_id: int,
        period_start: date,
        period_end: date
    ) -> ComplianceCheck:
        """檢查出勤合規性"""
        violations = []
        warnings = []
        
        # 取得期間內所有出勤記錄
        attendances = Attendance.objects.filter(
            employee__organization_id=organization_id,
            work_date__gte=period_start,
            work_date__lte=period_end
        )
        
        # 檢查異常出勤
        anomalies = attendances.filter(anomaly_flag=True)
        if anomalies.exists():
            violations.append({
                'type': 'attendance_anomaly',
                'count': anomalies.count(),
                'message': f'發現 {anomalies.count()} 筆異常出勤記錄'
            })
        
        # 檢查超時工作
        for attendance in attendances:
            if attendance.actual_hours and attendance.actual_hours > Decimal('12'):
                violations.append({
                    'type': 'overtime_violation',
                    'employee_id': attendance.employee.employee_id,
                    'date': attendance.work_date.isoformat(),
                    'hours': float(attendance.actual_hours),
                    'message': f'員工 {attendance.employee.employee_id} 於 {attendance.work_date} 工作超過 12 小時'
                })
        
        status = 'pass' if len(violations) == 0 else 'violation'
        
        compliance_check = ComplianceCheck.objects.create(
            organization_id=organization_id,
            check_type='attendance',
            check_period_start=period_start,
            check_period_end=period_end,
            status=status,
            violations=violations,
            warnings=warnings,
        )
        
        return compliance_check
    
    def _check_weekly_hours(
        self,
        schedules: List[Schedule],
        employee: Employee,
        max_hours: int
    ) -> List[Dict[str, Any]]:
        """檢查每週工時"""
        violations = []
        
        # 按週分組
        weekly_schedules = {}
        for schedule in schedules:
            week_start = schedule.schedule_date - timedelta(days=schedule.schedule_date.weekday())
            week_key = week_start.isoformat()
            
            if week_key not in weekly_schedules:
                weekly_schedules[week_key] = []
            weekly_schedules[week_key].append(schedule)
        
        # 檢查每週總工時
        for week_start, week_schedules in weekly_schedules.items():
            total_hours = sum(float(s.expected_hours) for s in week_schedules)
            if total_hours > max_hours:
                violations.append({
                    'type': 'weekly_hours_violation',
                    'employee_id': employee.employee_id,
                    'week_start': week_start,
                    'total_hours': total_hours,
                    'max_hours': max_hours,
                    'message': f'員工 {employee.employee_id} 在 {week_start} 當週工時 {total_hours} 小時，超過限制 {max_hours} 小時'
                })
        
        return violations
    
    def _check_consecutive_days(
        self,
        schedules: List[Schedule],
        max_days: int
    ) -> List[Dict[str, Any]]:
        """檢查連續工作天數"""
        violations = []
        
        if not schedules:
            return violations
        
        # 排序並找出連續工作天數
        sorted_schedules = sorted(schedules, key=lambda s: s.schedule_date)
        consecutive_count = 1
        max_consecutive = 1
        start_date = sorted_schedules[0].schedule_date
        
        for i in range(1, len(sorted_schedules)):
            prev_date = sorted_schedules[i-1].schedule_date
            curr_date = sorted_schedules[i].schedule_date
            
            if (curr_date - prev_date).days == 1:
                consecutive_count += 1
                max_consecutive = max(max_consecutive, consecutive_count)
            else:
                if consecutive_count > max_days:
                    violations.append({
                        'type': 'consecutive_days_violation',
                        'employee_id': sorted_schedules[0].employee.employee_id,
                        'start_date': start_date.isoformat(),
                        'consecutive_days': consecutive_count,
                        'max_days': max_days,
                        'message': f'員工連續工作 {consecutive_count} 天，超過限制 {max_days} 天'
                    })
                consecutive_count = 1
                start_date = curr_date
        
        # 檢查最後一段
        if consecutive_count > max_days:
            violations.append({
                'type': 'consecutive_days_violation',
                'employee_id': sorted_schedules[0].employee.employee_id,
                'start_date': start_date.isoformat(),
                'consecutive_days': consecutive_count,
                'max_days': max_days,
                'message': f'員工連續工作 {consecutive_count} 天，超過限制 {max_days} 天'
            })
        
        return violations
    
    def _check_rest_interval(
        self,
        schedules: List[Schedule],
        min_rest_hours: float
    ) -> List[Dict[str, Any]]:
        """檢查兩班之間休息間隔"""
        violations = []

        if len(schedules) < 2:
            return violations

        sorted_schedules = sorted(schedules, key=lambda s: (s.schedule_date, s.shift_template.start_time))

        for i in range(len(sorted_schedules) - 1):
            current = sorted_schedules[i]
            next_schedule = sorted_schedules[i + 1]

            # 以 datetime 計算休息時間，避免跨日與分鐘截斷問題
            current_end_dt = datetime.combine(current.schedule_date, current.shift_template.end_time)
            # 跨午夜班別：end_time < start_time 表示結束在下一個日曆日
            if current.shift_template.end_time < current.shift_template.start_time:
                current_end_dt += timedelta(days=1)
            next_start_dt = datetime.combine(next_schedule.schedule_date, next_schedule.shift_template.start_time)
            rest_hours = (next_start_dt - current_end_dt).total_seconds() / 3600

            if rest_hours < min_rest_hours:
                violations.append({
                    'type': 'rest_interval_violation',
                    'employee_id': current.employee.employee_id,
                    'date1': current.schedule_date.isoformat(),
                    'date2': next_schedule.schedule_date.isoformat(),
                    'rest_hours': round(rest_hours, 2),
                    'min_rest_hours': min_rest_hours,
                    'message': f'兩班之間休息時間 {round(rest_hours, 2)} 小時，低於限制 {min_rest_hours} 小時'
                })
        
        return violations
