"""
OR-Tools Provider Implementation
"""
import copy
from datetime import date, datetime, timedelta
from typing import List, Dict, Any
from ortools.sat.python import cp_model
from .base import (
    BaseScheduleProvider,
    ScheduleRequest,
    ScheduleResult,
    ComplianceReport,
    ChangeImpact,
)


class ORToolsProvider(BaseScheduleProvider):
    """使用 Google OR-Tools CP-SAT Solver 的內建實作"""

    # 勞基法預設值
    DEFAULT_MAX_WEEKLY_HOURS = 40
    DEFAULT_MIN_REST_HOURS = 11
    DEFAULT_MAX_CONSECUTIVE_DAYS = 6

    def generate_schedule(self, request: ScheduleRequest) -> ScheduleResult:
        """使用 OR-Tools CP-SAT 求解器產生排班表"""
        try:
            model = cp_model.CpModel()

            employees = request.employees
            shifts = request.shift_templates
            days = self._get_days_in_period(request.period_start, request.period_end)

            if not employees or not shifts or not days:
                return ScheduleResult(
                    success=False,
                    assignments=[],
                    score=float('inf'),
                    violations=[{'type': 'input_error', 'message': '員工、班別或日期範圍不可為空'}],
                    metadata={},
                    message='輸入資料不足，無法排班',
                )

            # 建立決策變數 assignments[emp_id][day_idx][shift_id]
            assignments = {}
            for emp in employees:
                emp_id = emp['id']
                assignments[emp_id] = {}
                for day_idx in range(len(days)):
                    assignments[emp_id][day_idx] = {}
                    for shift in shifts:
                        var_name = f"e{emp_id}_d{day_idx}_s{shift['id']}"
                        assignments[emp_id][day_idx][shift['id']] = model.NewBoolVar(var_name)

            # 硬約束
            self._add_hard_constraints(
                model, assignments, employees, shifts, days, request.constraints
            )

            # 軟約束（目標函數）
            objective_terms = self._add_soft_constraints(
                model, assignments, employees, shifts, days,
                request.constraints, request.preferences,
            )

            if objective_terms:
                model.Minimize(sum(objective_terms))

            # 求解
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 300.0
            status = solver.Solve(model)

            if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                result_assignments = self._extract_assignments(
                    solver, assignments, employees, shifts, days
                )
                return ScheduleResult(
                    success=True,
                    assignments=result_assignments,
                    score=solver.ObjectiveValue(),
                    violations=[],
                    metadata={
                        'solver': 'OR-Tools CP-SAT',
                        'solve_time_seconds': solver.WallTime(),
                        'status': 'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE',
                        'mode': 'generate',
                    },
                )
            else:
                return ScheduleResult(
                    success=False,
                    assignments=[],
                    score=float('inf'),
                    violations=[{'type': 'solver_failed', 'message': 'No feasible solution found'}],
                    metadata={'solver': 'OR-Tools CP-SAT', 'status': 'INFEASIBLE'},
                    message='無法找到可行解，請檢查約束條件（員工數量、證照需求等）',
                )

        except Exception as e:
            return ScheduleResult(
                success=False,
                assignments=[],
                score=float('inf'),
                violations=[{'type': 'error', 'message': str(e)}],
                metadata={'error': str(e)},
                message=f'求解過程發生錯誤: {str(e)}',
            )

    def optimize_schedule(
        self, current_schedule: Dict[str, Any], constraints: Dict[str, Any]
    ) -> ScheduleResult:
        """
        優化現有排班表。
        以現有指派為高偏好軟約束重新求解，在滿足硬約束的前提下儘量保留原排班。
        """
        try:
            period_start_raw = constraints.get('period_start')
            period_end_raw = constraints.get('period_end')
            if not period_start_raw or not period_end_raw:
                return ScheduleResult(
                    success=False, assignments=[], score=float('inf'),
                    violations=[], metadata={},
                    message='constraints 必須包含 period_start 和 period_end',
                )
            period_start = (
                date.fromisoformat(period_start_raw)
                if isinstance(period_start_raw, str) else period_start_raw
            )
            period_end = (
                date.fromisoformat(period_end_raw)
                if isinstance(period_end_raw, str) else period_end_raw
            )
        except (ValueError, TypeError) as e:
            return ScheduleResult(
                success=False, assignments=[], score=float('inf'),
                violations=[], metadata={}, message=f'日期格式錯誤：{e}',
            )

        # 將現有排班轉為高偏好設定（偏好值 10 = 盡量保留）
        existing_preferences: Dict[str, Dict[str, int]] = {}
        for a in current_schedule.get('assignments', []):
            emp_key = str(a['employee_id'])
            shift_key = str(a['shift_id'])
            if emp_key not in existing_preferences:
                existing_preferences[emp_key] = {}
            existing_preferences[emp_key][shift_key] = 10

        request = ScheduleRequest(
            organization_id=constraints.get('organization_id', 0),
            branch_id=constraints.get('branch_id'),
            period_start=period_start,
            period_end=period_end,
            employees=current_schedule.get('employees', []),
            shift_templates=current_schedule.get('shift_templates', []),
            constraints=constraints,
            preferences=existing_preferences,
        )

        result = self.generate_schedule(request)
        if result.success:
            result.metadata['mode'] = 'optimized'
        return result

    def check_compliance(self, schedule: Dict[str, Any]) -> ComplianceReport:
        """
        依排班資料 dict 檢查勞基法合規性。
        schedule 格式：
          {
            'assignments': [{'employee_id', 'date', 'shift_id'}, ...],
            'shift_templates': [{'id', 'start_time', 'end_time', 'break_minutes'}, ...],
            'constraints': {'max_weekly_hours', 'min_rest_hours', 'max_consecutive_days'},
          }
        """
        violations: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []

        assignments = schedule.get('assignments', [])
        shift_map = {s['id']: s for s in schedule.get('shift_templates', [])}
        constraints = schedule.get('constraints', {})

        max_weekly_hours = constraints.get('max_weekly_hours', self.DEFAULT_MAX_WEEKLY_HOURS)
        min_rest_hours = constraints.get('min_rest_hours', self.DEFAULT_MIN_REST_HOURS)
        max_consecutive_days = constraints.get('max_consecutive_days', self.DEFAULT_MAX_CONSECUTIVE_DAYS)

        # 依員工分組
        by_employee: Dict[Any, List[Dict]] = {}
        for a in assignments:
            emp_id = a['employee_id']
            by_employee.setdefault(emp_id, []).append(a)

        for emp_id, emp_assignments in by_employee.items():
            sorted_a = sorted(emp_assignments, key=lambda x: x['date'])

            # 每週工時
            weekly_hours: Dict[str, float] = {}
            for a in sorted_a:
                d = date.fromisoformat(a['date']) if isinstance(a['date'], str) else a['date']
                week_key = (d - timedelta(days=d.weekday())).isoformat()
                shift = shift_map.get(a['shift_id'], {})
                hours = self._shift_duration_hours(shift)
                weekly_hours[week_key] = weekly_hours.get(week_key, 0.0) + hours

            for week_key, total in weekly_hours.items():
                if total > max_weekly_hours:
                    violations.append({
                        'type': 'weekly_hours_violation',
                        'employee_id': emp_id,
                        'week_start': week_key,
                        'total_hours': round(total, 2),
                        'max_hours': max_weekly_hours,
                        'message': (
                            f'員工 {emp_id} 於 {week_key} 當週工時 {round(total, 2)} 小時，'
                            f'超過限制 {max_weekly_hours} 小時'
                        ),
                    })

            # 連續工作天數
            unique_dates = sorted(set(a['date'] for a in sorted_a))
            if unique_dates:
                consec = 1
                seg_start = unique_dates[0]
                for i in range(1, len(unique_dates)):
                    d_prev = date.fromisoformat(unique_dates[i - 1])
                    d_curr = date.fromisoformat(unique_dates[i])
                    if (d_curr - d_prev).days == 1:
                        consec += 1
                    else:
                        if consec > max_consecutive_days:
                            violations.append({
                                'type': 'consecutive_days_violation',
                                'employee_id': emp_id,
                                'start_date': seg_start,
                                'consecutive_days': consec,
                                'max_days': max_consecutive_days,
                                'message': (
                                    f'員工 {emp_id} 自 {seg_start} 起連續工作 {consec} 天，'
                                    f'超過限制 {max_consecutive_days} 天'
                                ),
                            })
                        consec = 1
                        seg_start = unique_dates[i]
                if consec > max_consecutive_days:
                    violations.append({
                        'type': 'consecutive_days_violation',
                        'employee_id': emp_id,
                        'start_date': seg_start,
                        'consecutive_days': consec,
                        'max_days': max_consecutive_days,
                        'message': (
                            f'員工 {emp_id} 自 {seg_start} 起連續工作 {consec} 天，'
                            f'超過限制 {max_consecutive_days} 天'
                        ),
                    })

            # 休息間隔
            if len(sorted_a) >= 2:
                for i in range(len(sorted_a) - 1):
                    curr = sorted_a[i]
                    nxt = sorted_a[i + 1]
                    curr_shift = shift_map.get(curr['shift_id'], {})
                    nxt_shift = shift_map.get(nxt['shift_id'], {})

                    curr_date = date.fromisoformat(curr['date']) if isinstance(curr['date'], str) else curr['date']
                    nxt_date = date.fromisoformat(nxt['date']) if isinstance(nxt['date'], str) else nxt['date']

                    curr_end_dt = datetime.combine(
                        curr_date,
                        datetime.strptime(curr_shift.get('end_time', '23:59'), '%H:%M').time(),
                    )
                    nxt_start_dt = datetime.combine(
                        nxt_date,
                        datetime.strptime(nxt_shift.get('start_time', '00:00'), '%H:%M').time(),
                    )
                    rest_h = (nxt_start_dt - curr_end_dt).total_seconds() / 3600

                    if 0 < rest_h < min_rest_hours:
                        violations.append({
                            'type': 'rest_interval_violation',
                            'employee_id': emp_id,
                            'date1': curr['date'],
                            'date2': nxt['date'],
                            'rest_hours': round(rest_h, 2),
                            'min_rest_hours': min_rest_hours,
                            'message': (
                                f'員工 {emp_id} 兩班休息時間 {round(rest_h, 2)} 小時，'
                                f'低於限制 {min_rest_hours} 小時'
                            ),
                        })

        return ComplianceReport(
            is_compliant=len(violations) == 0,
            violations=violations,
            warnings=warnings,
            details={
                'total_assignments': len(assignments),
                'employees_checked': len(by_employee),
                'rules_applied': {
                    'max_weekly_hours': max_weekly_hours,
                    'min_rest_hours': min_rest_hours,
                    'max_consecutive_days': max_consecutive_days,
                },
            },
        )

    def evaluate_change(
        self, schedule: Dict[str, Any], proposed_change: Dict[str, Any]
    ) -> ChangeImpact:
        """
        評估排班異動影響。
        proposed_change 格式：
          {
            'type': 'substitute' | 'cancel' | 'modify',
            'employee_id': <原員工 db id>,
            'date': 'YYYY-MM-DD',
            'shift_id': <班別 id>,
            'new_employee_id': <代班員工 id>  (substitute 必填),
            'new_shift_id': <新班別 id>       (modify 選填),
            'new_date': 'YYYY-MM-DD'          (modify 選填),
          }
        """
        affected_employees: set = set()
        old_emp_id = proposed_change.get('employee_id')
        new_emp_id = proposed_change.get('new_employee_id')
        if old_emp_id is not None:
            affected_employees.add(old_emp_id)
        if new_emp_id is not None:
            affected_employees.add(new_emp_id)

        # 套用異動到記憶體副本
        modified_schedule = self._apply_change(schedule, proposed_change)

        # 原始合規報告
        original_report = self.check_compliance(schedule)
        # 異動後合規報告
        modified_report = self.check_compliance(modified_schedule)

        # 找出異動後新增的 violations
        original_msgs = {v.get('message') for v in original_report.violations}
        new_violations = [v for v in modified_report.violations if v.get('message') not in original_msgs]

        original_warn_msgs = {w.get('message') for w in original_report.warnings}
        new_warnings = [w for w in modified_report.warnings if w.get('message') not in original_warn_msgs]

        can_apply = len(new_violations) == 0
        impact_score = float(len(new_violations)) * 2.0 + float(len(new_warnings)) * 0.5

        return ChangeImpact(
            can_apply=can_apply,
            impact_score=impact_score,
            violations=new_violations,
            warnings=new_warnings,
            affected_employees=list(affected_employees),
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _apply_change(
        self, schedule: Dict[str, Any], proposed_change: Dict[str, Any]
    ) -> Dict[str, Any]:
        """在記憶體中套用排班異動"""
        modified = copy.deepcopy(schedule)
        assignments = modified.get('assignments', [])

        change_type = proposed_change.get('type', 'modify')
        target_emp = proposed_change.get('employee_id')
        target_date = proposed_change.get('date')
        target_shift = proposed_change.get('shift_id')

        if change_type == 'substitute':
            new_emp = proposed_change.get('new_employee_id')
            for a in assignments:
                if (a['employee_id'] == target_emp
                        and a['date'] == target_date
                        and a['shift_id'] == target_shift):
                    a['employee_id'] = new_emp
                    break

        elif change_type == 'cancel':
            modified['assignments'] = [
                a for a in assignments
                if not (a['employee_id'] == target_emp
                        and a['date'] == target_date
                        and a['shift_id'] == target_shift)
            ]

        elif change_type == 'modify':
            new_shift = proposed_change.get('new_shift_id', target_shift)
            new_date = proposed_change.get('new_date', target_date)
            for a in assignments:
                if (a['employee_id'] == target_emp
                        and a['date'] == target_date
                        and a['shift_id'] == target_shift):
                    a['shift_id'] = new_shift
                    a['date'] = new_date
                    break

        return modified

    @staticmethod
    def _times_overlap(s1_str: str, e1_str: str, s2_str: str, e2_str: str) -> bool:
        """
        判斷兩個時段是否重疊（支援跨午夜，例如 22:00-06:00）。
        入參為 'HH:MM' 字串。
        """
        def to_min(t_str: str) -> int:
            h, m = map(int, t_str.split(':'))
            return h * 60 + m

        s1, e1 = to_min(s1_str), to_min(e1_str)
        s2, e2 = to_min(s2_str), to_min(e2_str)

        # 跨午夜處理（end < start 表示跨過 00:00）
        if e1 <= s1: e1 += 1440
        if e2 <= s2: e2 += 1440

        return s1 < e2 and s2 < e1

    def _shift_duration_hours(self, shift: Dict[str, Any]) -> float:
        """計算班別工時（小時）"""
        start_str = shift.get('start_time', '00:00')
        end_str = shift.get('end_time', '00:00')
        break_minutes = shift.get('break_minutes', 0)
        try:
            start_t = datetime.strptime(start_str, '%H:%M').time()
            end_t = datetime.strptime(end_str, '%H:%M').time()
        except ValueError:
            return 0.0
        ref = date.today()
        start_dt = datetime.combine(ref, start_t)
        end_dt = datetime.combine(ref, end_t)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        return max(0.0, (end_dt - start_dt).total_seconds() / 3600 - break_minutes / 60)

    def _get_days_in_period(self, start: date, end: date) -> List[date]:
        """取得期間內的所有日期"""
        days = []
        current = start
        while current <= end:
            days.append(current)
            current += timedelta(days=1)
        return days

    def _add_hard_constraints(
        self,
        model: cp_model.CpModel,
        assignments: Dict,
        employees: List[Dict],
        shifts: List[Dict],
        days: List[date],
        constraints: Dict[str, Any],
    ):
        """加入硬約束"""
        num_days = len(days)

        # 1. 每個班別每天至少需要 min_staff_count 人
        for day_idx in range(num_days):
            for shift in shifts:
                shift_id = shift['id']
                min_staff = shift.get('min_staff_count', 1)
                model.Add(
                    sum(assignments[emp['id']][day_idx][shift_id] for emp in employees) >= min_staff
                )

        # 2. 每個員工每天最多排一個班別
        for emp in employees:
            emp_id = emp['id']
            for day_idx in range(num_days):
                model.Add(sum(assignments[emp_id][day_idx].values()) <= 1)

        # 3. 員工可用性（不可用日期禁止排班）
        for emp in employees:
            emp_id = emp['id']
            unavailable_dates = set(emp.get('unavailable_dates', []))
            for day_idx, day in enumerate(days):
                if day.isoformat() in unavailable_dates:
                    for shift in shifts:
                        model.Add(assignments[emp_id][day_idx][shift['id']] == 0)

        # 4. 員工必須持有班別所需的全部證照
        for shift in shifts:
            shift_id = shift['id']
            required_certs = set(shift.get('required_certifications', []))
            if not required_certs:
                continue
            for emp in employees:
                emp_id = emp['id']
                emp_certs = set(emp.get('certifications', []))
                if not required_certs.issubset(emp_certs):
                    for day_idx in range(num_days):
                        model.Add(assignments[emp_id][day_idx][shift_id] == 0)

        # 5. 員工 blocked 時段：若班別與封鎖時段重疊，禁止排班（硬約束）
        for emp in employees:
            emp_id = emp['id']
            blocked_slots = emp.get('availability', {}).get('blocked_slots', [])
            if not blocked_slots:
                continue
            for day_idx, day in enumerate(days):
                dow = day.weekday()  # 0=Mon … 6=Sun
                for shift in shifts:
                    shift_start = shift.get('start_time', '00:00')
                    shift_end = shift.get('end_time', '00:00')
                    for slot in blocked_slots:
                        slot_dow = slot.get('day_of_week')   # None = 每天
                        if slot_dow is not None and slot_dow != dow:
                            continue
                        if self._times_overlap(
                            shift_start, shift_end,
                            slot['start_time'], slot['end_time'],
                        ):
                            model.Add(assignments[emp_id][day_idx][shift['id']] == 0)
                            break  # 已封鎖，不需再判斷其他 slot

    def _add_soft_constraints(
        self,
        model: cp_model.CpModel,
        assignments: Dict,
        employees: List[Dict],
        shifts: List[Dict],
        days: List[date],
        constraints: Dict[str, Any],
        preferences: Dict[str, Any],
    ) -> List:
        """加入軟約束（目標函數項）"""
        objective_terms = []

        if not employees:
            return objective_terms

        num_days = len(days)
        num_shifts = len(shifts)
        max_possible = num_days * num_shifts

        # --- 1. 公平分配：最小化員工班次數的最大差距 ---
        shift_counts = {}
        for emp in employees:
            emp_id = emp['id']
            shift_counts[emp_id] = model.NewIntVar(0, max_possible, f'cnt_{emp_id}')
            model.Add(
                shift_counts[emp_id] == sum(
                    assignments[emp_id][day_idx][shift['id']]
                    for day_idx in range(num_days)
                    for shift in shifts
                )
            )

        max_shifts_var = model.NewIntVar(0, max_possible, 'max_shifts')
        min_shifts_var = model.NewIntVar(0, max_possible, 'min_shifts')
        for emp in employees:
            model.Add(shift_counts[emp['id']] <= max_shifts_var)
            model.Add(shift_counts[emp['id']] >= min_shifts_var)

        disparity = model.NewIntVar(0, max_possible, 'disparity')
        model.Add(disparity == max_shifts_var - min_shifts_var)
        # 公平性權重 10：優先消除班次分配極端不均
        objective_terms.append(10 * disparity)

        # --- 2. 員工班別偏好（preferences dict）---
        # preferences 格式：{str(employee_db_id): {str(shift_id): score (0–10)}}
        for emp in employees:
            emp_id = emp['id']
            emp_prefs = preferences.get(str(emp_id), {})
            if not emp_prefs:
                continue
            for day_idx in range(num_days):
                for shift in shifts:
                    shift_id = shift['id']
                    pref_score = int(emp_prefs.get(str(shift_id), 5))
                    penalty = max(0, 10 - pref_score)
                    if penalty > 0:
                        objective_terms.append(penalty * assignments[emp_id][day_idx][shift_id])

        # --- 3. 員工 preferred 時段：排到非偏好班別時加懲罰（軟約束）---
        for emp in employees:
            emp_id = emp['id']
            preferred_slots = emp.get('availability', {}).get('preferred_slots', [])
            if not preferred_slots:
                continue
            for day_idx, day in enumerate(days):
                dow = day.weekday()
                for shift in shifts:
                    shift_start = shift.get('start_time', '00:00')
                    shift_end = shift.get('end_time', '00:00')
                    is_preferred = any(
                        (slot.get('day_of_week') is None or slot.get('day_of_week') == dow)
                        and self._times_overlap(
                            shift_start, shift_end,
                            slot['start_time'], slot['end_time'],
                        )
                        for slot in preferred_slots
                    )
                    if not is_preferred:
                        # 排到非偏好時段，懲罰值 3（低於公平性權重 10，確保公平優先）
                        objective_terms.append(3 * assignments[emp_id][day_idx][shift['id']])

        # --- 4. 員工每週所需工時軟約束 ---
        # 若 availability.required_hours_per_week 有設定，嘗試滿足
        # 以週為單位，對每週分配的工時偏差加懲罰
        if num_days >= 7:
            for emp in employees:
                emp_id = emp['id']
                required_h = emp.get('availability', {}).get('required_hours_per_week')
                if required_h is None:
                    continue
                # 換算為約幾個班次
                avg_shift_h = (
                    sum(self._shift_duration_hours(s) for s in shifts) / len(shifts)
                    if shifts else 8.0
                )
                target_shifts_per_week = max(1, int(round(float(required_h) / avg_shift_h)))

                # 以整個排班期間估算目標班次數
                num_weeks = num_days / 7.0
                total_target = max(1, int(round(target_shifts_per_week * num_weeks)))

                over = model.NewIntVar(0, max_possible, f'over_req_{emp_id}')
                under = model.NewIntVar(0, max_possible, f'under_req_{emp_id}')
                model.Add(shift_counts[emp_id] - total_target <= over)
                model.Add(total_target - shift_counts[emp_id] <= under)
                # 低於需求工時懲罰更重（× 5），超過則較輕（× 2）
                objective_terms.append(5 * under + 2 * over)

        return objective_terms

    def _extract_assignments(
        self,
        solver: cp_model.CpSolver,
        assignments: Dict,
        employees: List[Dict],
        shifts: List[Dict],
        days: List[date],
    ) -> List[Dict[str, Any]]:
        """從求解結果中提取排班指派"""
        result = []
        for emp in employees:
            emp_id = emp['id']
            for day_idx, day in enumerate(days):
                for shift in shifts:
                    shift_id = shift['id']
                    if solver.Value(assignments[emp_id][day_idx][shift_id]) == 1:
                        result.append({
                            'employee_id': emp_id,
                            'date': day.isoformat(),
                            'shift_id': shift_id,
                            'shift_name': shift.get('name', ''),
                        })
        return result
