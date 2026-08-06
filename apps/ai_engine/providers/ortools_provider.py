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
            # 勞基法硬約束：派生 A 模式（一律強制）或顯式 enforce_labor_law=True。
            # 純生成且未顯式啟用時保持舊行為，由外部 compliance check 驗證。
            drift_mode = request.minimize_drift_from_seed and request.seed
            labor_soft_terms: List = []
            if drift_mode or request.enforce_labor_law:
                labor_soft_terms = self._add_labor_law_hard_constraints(
                    model, assignments, employees, shifts, days, request.constraints,
                    soft_rule_types=request.soft_labor_rules,
                )
            # 團隊規則（性別/身高/證照等）：以 CP-SAT 動態翻譯
            tc_soft_terms: List = []
            if request.team_constraints:
                from ..team_constraint_compiler import apply_team_constraints
                tc_soft_terms = apply_team_constraints(
                    model, assignments, employees, shifts, days,
                    request.team_constraints, request.branch_id,
                )

            # 軟約束（目標函數）
            objective_terms = self._add_soft_constraints(
                model, assignments, employees, shifts, days,
                request.constraints, request.preferences,
            )

            # Team constraint 軟約束 penalty 項
            if tc_soft_terms:
                objective_terms.extend(tc_soft_terms)

            # 軟性勞基法規則 penalty 項（PR11）
            if labor_soft_terms:
                objective_terms.extend(labor_soft_terms)

            # Drift objective：派生 A 模式啟用，按時間遞減權重最小化變更格子數
            if request.minimize_drift_from_seed and request.seed:
                objective_terms.extend(self._add_drift_objective(
                    model, assignments, employees, shifts, days,
                    seed=request.seed,
                    today=request.today or date.today(),
                    time_decay_n=request.time_decay_n,
                    drift_weight=request.drift_weight,
                ))

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
                mode = ('derive_legal'
                        if request.minimize_drift_from_seed and request.seed
                        else 'generate')
                return ScheduleResult(
                    success=True,
                    assignments=result_assignments,
                    score=solver.ObjectiveValue(),
                    violations=[],
                    metadata={
                        'solver': 'OR-Tools CP-SAT',
                        'solve_time_seconds': solver.WallTime(),
                        'status': 'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE',
                        'mode': mode,
                        'time_decay_n': request.time_decay_n if mode == 'derive_legal' else None,
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
            # Sort by (date, start time) so multi-shift days keep intra-day
            # order and the cross-day rest check pairs the true last/first shifts.
            sorted_a = sorted(
                emp_assignments,
                key=lambda x: (
                    str(x['date']),
                    shift_map.get(x['shift_id'], {}).get('start_time', '00:00'),
                ),
            )

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

            # 休息間隔（只比不同工作日的相鄰班：同日拆班是合法的多班日，
            # 不構成休息違規）
            if len(sorted_a) >= 2:
                for i in range(len(sorted_a) - 1):
                    curr = sorted_a[i]
                    nxt = sorted_a[i + 1]
                    curr_shift = shift_map.get(curr['shift_id'], {})
                    nxt_shift = shift_map.get(nxt['shift_id'], {})

                    curr_date = date.fromisoformat(curr['date']) if isinstance(curr['date'], str) else curr['date']
                    nxt_date = date.fromisoformat(nxt['date']) if isinstance(nxt['date'], str) else nxt['date']

                    if curr_date == nxt_date:
                        continue

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
            # Accept 'HH:MM' and 'HH:MM:SS' (DB TimeField str() gives seconds)
            parts = str(t_str).split(':')
            return int(parts[0]) * 60 + int(parts[1])

        s1, e1 = to_min(s1_str), to_min(e1_str)
        s2, e2 = to_min(s2_str), to_min(e2_str)

        # 跨午夜處理（end < start 表示跨過 00:00）
        if e1 <= s1: e1 += 1440
        if e2 <= s2: e2 += 1440

        return s1 < e2 and s2 < e1

    def _shift_duration_hours(self, shift: Dict[str, Any]) -> float:
        """計算班別工時（小時）"""
        start_str = str(shift.get('start_time', '00:00'))
        end_str = str(shift.get('end_time', '00:00'))
        break_minutes = shift.get('break_minutes', 0)
        try:
            # Accept 'HH:MM' and 'HH:MM:SS' (TimeField isoformat carries seconds)
            start_t = datetime.strptime(start_str[:5], '%H:%M').time()
            end_t = datetime.strptime(end_str[:5], '%H:%M').time()
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

        # 2. 同員工同日允許多個班別（multi-shift），但時間重疊的兩班互斥
        #    （AI 不主動產生重疊班；手動排班仍可重疊，鎖只在 solver）
        overlapping_pairs = [
            (a['id'], b['id'])
            for i, a in enumerate(shifts)
            for b in shifts[i + 1:]
            if self._times_overlap(
                a.get('start_time', '00:00'), a.get('end_time', '00:00'),
                b.get('start_time', '00:00'), b.get('end_time', '00:00'),
            )
        ]
        for emp in employees:
            emp_id = emp['id']
            for day_idx in range(num_days):
                for shift_a, shift_b in overlapping_pairs:
                    model.Add(
                        assignments[emp_id][day_idx][shift_a]
                        + assignments[emp_id][day_idx][shift_b] <= 1
                    )

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

        # --- 4. 班別員工優先順序（超時加班意願）---
        # 每個班別可設定優先順序清單；排入優先順序外的員工加懲罰。
        # rank → OR-Tools weight: 1→10, 2→7, 3→4, 4→2, 5+→1, 不在清單→0
        RANK_WEIGHT = {1: 10, 2: 7, 3: 4, 4: 2}

        for shift in shifts:
            shift_id = shift['id']
            priority_list = shift.get('employee_priorities', [])
            if not priority_list:
                continue

            priority_map: Dict[int, int] = {}
            for p in priority_list:
                rank = p.get('priority_rank', 99)
                priority_map[p['employee_id']] = RANK_WEIGHT.get(rank, 1) if rank <= 4 else 1

            for emp in employees:
                emp_id = emp['id']
                emp_weight = priority_map.get(emp_id, 0)
                # 不在清單 weight=0，懲罰最大；排名越高懲罰越小
                penalty = 10 - emp_weight
                if penalty <= 0:
                    continue
                for day_idx in range(num_days):
                    objective_terms.append(penalty * assignments[emp_id][day_idx][shift_id])

            # max_extra_shifts 上限（軟約束，重罰阻止超出）
            for p in priority_list:
                max_extra = p.get('max_extra_shifts')
                if max_extra is None:
                    continue
                emp_id = p['employee_id']
                if not any(e['id'] == emp_id for e in employees):
                    continue
                total_on_shift = model.NewIntVar(0, num_days, f'tot_s{shift_id}_e{emp_id}')
                model.Add(
                    total_on_shift == sum(
                        assignments[emp_id][day_idx][shift_id]
                        for day_idx in range(num_days)
                    )
                )
                over_cap = model.NewIntVar(0, num_days, f'ocap_s{shift_id}_e{emp_id}')
                model.Add(over_cap >= total_on_shift - max_extra)
                objective_terms.append(20 * over_cap)

        # --- 5. 員工每週所需工時軟約束 ---
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

        # --- 6. 員工排班模式偏好（PR9: 花花班 vs 連上長假）---
        objective_terms.extend(
            self._add_pattern_preference_terms(
                model, assignments, employees, shifts, days
            )
        )

        return objective_terms

    # Pattern-preference penalty weights. Chosen below the fairness weight
    # (=10) so they only act as a tie-breaker; raising them would let an
    # individual's preference outweigh team-wide fairness, which is the
    # opposite of what the customer asked for.
    PATTERN_PENALTY_PER_PAIR = 2

    def _add_pattern_preference_terms(
        self,
        model: cp_model.CpModel,
        assignments: Dict,
        employees: List[Dict],
        shifts: List[Dict],
        days: List[date],
    ) -> List:
        """
        Translate `Employee.shift_pattern_preference` into soft penalties.

        Two preferences are supported; `none` is a no-op:

          * `alternating` — staff who like 花花班. Penalty per pair of
            consecutive days where the employee works the same time-of-day
            bucket on both days (e.g. morning Mon AND morning Tue).
          * `consecutive` — staff who like 連上放長假. Penalty per work/rest
            transition between consecutive days, so the solver prefers
            packing work-days together with rest-days together.
        """
        from ..team_constraint_compiler import _bucket_for_start_time

        terms: List = []
        num_days = len(days)
        if num_days < 2 or not employees or not shifts:
            return terms

        # Group shifts by time-of-day bucket so we can express "any morning
        # shift on day d" with a sum over the bucket's shift ids.
        bucket_of_shift = {
            s['id']: _bucket_for_start_time(s.get('start_time', '00:00'))
            for s in shifts
        }
        # Avoid n^2 over shifts inside the per-employee loop.
        shifts_by_bucket: Dict[str, List[int]] = {}
        for sid, bucket in bucket_of_shift.items():
            shifts_by_bucket.setdefault(bucket, []).append(sid)

        for emp in employees:
            pref = (emp.get('attributes') or {}).get('shift_pattern_preference', 'none')
            if pref == 'none' or pref is None:
                continue
            emp_id = emp['id']

            if pref == 'alternating':
                # Penalty per consecutive same-bucket assignment. For each
                # bucket and each pair of consecutive days we add a Boolean
                # `both` that the solver will force to 1 iff the employee
                # works the bucket on both days; the linearisation uses
                # three half-implications (both ≤ x, both ≤ y, both ≥ x+y-1).
                for bucket, sids in shifts_by_bucket.items():
                    if not sids:
                        continue
                    for day_idx in range(num_days - 1):
                        # x = 1 iff employee works *any* shift in this
                        # bucket on day_idx; y same for day_idx + 1.
                        x = model.NewBoolVar(
                            f'patt_alt_x_e{emp_id}_d{day_idx}_b{bucket}'
                        )
                        y = model.NewBoolVar(
                            f'patt_alt_y_e{emp_id}_d{day_idx + 1}_b{bucket}'
                        )
                        # Reified link (not ==): with multi-shift the sum can
                        # exceed 1, which would make a Bool equality infeasible.
                        x_sum = sum(assignments[emp_id][day_idx][sid] for sid in sids)
                        model.Add(x_sum >= 1).OnlyEnforceIf(x)
                        model.Add(x_sum == 0).OnlyEnforceIf(x.Not())
                        y_sum = sum(assignments[emp_id][day_idx + 1][sid] for sid in sids)
                        model.Add(y_sum >= 1).OnlyEnforceIf(y)
                        model.Add(y_sum == 0).OnlyEnforceIf(y.Not())
                        both = model.NewBoolVar(
                            f'patt_alt_both_e{emp_id}_d{day_idx}_b{bucket}'
                        )
                        model.Add(both <= x)
                        model.Add(both <= y)
                        model.Add(both >= x + y - 1)
                        terms.append(self.PATTERN_PENALTY_PER_PAIR * both)

            elif pref == 'consecutive':
                # Penalty per work/rest transition between consecutive days.
                # daily_work[d] = 1 iff the employee works any shift on day d.
                # diff[d] = |daily_work[d] - daily_work[d+1]| (= XOR for binaries).
                # The model minimises diff, so it equals the actual XOR.
                daily_work = []
                for day_idx in range(num_days):
                    dw = model.NewBoolVar(f'patt_dwork_e{emp_id}_d{day_idx}')
                    # Reified link (not ==): multi-shift days have sum > 1.
                    day_sum = sum(
                        assignments[emp_id][day_idx][s['id']] for s in shifts
                    )
                    model.Add(day_sum >= 1).OnlyEnforceIf(dw)
                    model.Add(day_sum == 0).OnlyEnforceIf(dw.Not())
                    daily_work.append(dw)
                for day_idx in range(num_days - 1):
                    diff = model.NewBoolVar(
                        f'patt_cons_diff_e{emp_id}_d{day_idx}'
                    )
                    model.Add(diff >= daily_work[day_idx] - daily_work[day_idx + 1])
                    model.Add(diff >= daily_work[day_idx + 1] - daily_work[day_idx])
                    terms.append(self.PATTERN_PENALTY_PER_PAIR * diff)

        return terms

    # Soft labour-law violation weight. Far above any preference weight so
    # the solver only ever leaves a soft labour-law rule violated when there
    # is literally no feasible alternative — but unlike a hard constraint it
    # will not make the whole solve INFEASIBLE. Customer wants every
    # labour-law hit *shown*, not necessarily *blocked*.
    SOFT_LABOR_WEIGHT = 50

    def _add_labor_law_hard_constraints(
        self,
        model: cp_model.CpModel,
        assignments: Dict,
        employees: List[Dict],
        shifts: List[Dict],
        days: List[date],
        constraints: Dict[str, Any],
        soft_rule_types=None,
    ) -> List:
        """
        Apply labour-law rules. A rule whose type is listed in
        `soft_rule_types` becomes an objective *penalty* (heavy weight,
        won't cause INFEASIBLE); every other rule is a hard CP-SAT
        constraint. Returns the list of soft penalty terms for the caller
        to fold into Minimize(); hard constraints are added in place.

        Rules:
          1. max_weekly_hours      — Σ shift minutes within an ISO week ≤ cap
          2. max_consecutive_days  — any sliding window of (cap+1) calendar
             days has at most `cap` working days
          3. min_rest_hours        — for every ordered (day_i, shift_a) →
             (day_j, shift_b) pair whose rest gap < threshold (cross-midnight
             aware), the two cells cannot both go to the same employee.
        """
        soft = set(soft_rule_types or ())
        penalty_terms: List = []
        num_days = len(days)
        if num_days == 0 or not employees or not shifts:
            return penalty_terms

        max_weekly_minutes = int(round(
            float(constraints.get('max_weekly_hours', 40)) * 60
        ))
        max_consec = int(constraints.get('max_consecutive_days', 6))
        min_rest_hours = float(constraints.get('min_rest_hours', 11))

        weekly_soft = 'max_weekly_hours' in soft
        consec_soft = 'max_consecutive_days' in soft
        rest_soft = 'min_rest_hours' in soft

        # --- 1. weekly hours ---
        shift_minutes = {
            s['id']: int(round(self._shift_duration_hours(s) * 60))
            for s in shifts
        }
        week_groups: Dict[date, List[int]] = {}
        for day_idx, day in enumerate(days):
            wk = day - timedelta(days=day.weekday())
            week_groups.setdefault(wk, []).append(day_idx)
        for emp in employees:
            emp_id = emp['id']
            for wk, week_idxs in week_groups.items():
                week_minutes = sum(
                    shift_minutes[s['id']] * assignments[emp_id][di][s['id']]
                    for di in week_idxs
                    for s in shifts
                )
                if weekly_soft:
                    # over ≥ excess minutes; minimise → over = max(0, excess).
                    over = model.NewIntVar(
                        0, max_weekly_minutes * len(week_idxs) + 1,
                        f'soft_wk_e{emp_id}_{wk.isoformat()}',
                    )
                    model.Add(week_minutes - max_weekly_minutes <= over)
                    # Scale down minutes → ~hours so the weight is comparable
                    # to other rules' per-unit penalties (1 unit ≈ 1 hour over).
                    penalty_terms.append(self.SOFT_LABOR_WEIGHT * over)
                else:
                    model.Add(week_minutes <= max_weekly_minutes)

        # --- 2. consecutive days ---
        if num_days >= max_consec + 1:
            for emp in employees:
                emp_id = emp['id']
                row = []
                for day_idx in range(num_days):
                    dw = model.NewBoolVar(f'dwork_e{emp_id}_d{day_idx}')
                    # Reified link (not ==): multi-shift days have sum > 1.
                    day_sum = sum(
                        assignments[emp_id][day_idx][s['id']] for s in shifts
                    )
                    model.Add(day_sum >= 1).OnlyEnforceIf(dw)
                    model.Add(day_sum == 0).OnlyEnforceIf(dw.Not())
                    row.append(dw)
                window = max_consec + 1
                for start in range(num_days - window + 1):
                    window_sum = sum(row[start:start + window])
                    if consec_soft:
                        over = model.NewIntVar(
                            0, window,
                            f'soft_cons_e{emp_id}_w{start}',
                        )
                        model.Add(window_sum - max_consec <= over)
                        penalty_terms.append(self.SOFT_LABOR_WEIGHT * over)
                    else:
                        model.Add(window_sum <= max_consec)

        # --- 3. minimum rest hours ---
        # Same-day pairs are exempt: a split shift (e.g. 08-12 + 13-17) is a
        # legitimate multi-shift day, not a rest violation. The rule only
        # guards the gap between different working days.
        for i, day_i in enumerate(days):
            for j, day_j in enumerate(days):
                if (day_j - day_i).days < 1 or (day_j - day_i).days > 2:
                    continue
                for shift_a in shifts:
                    end_dt = self._shift_end_datetime(day_i, shift_a)
                    for shift_b in shifts:
                        start_dt = self._shift_start_datetime(day_j, shift_b)
                        if start_dt <= end_dt:
                            continue
                        rest_h = (start_dt - end_dt).total_seconds() / 3600
                        if rest_h >= min_rest_hours:
                            continue
                        for emp in employees:
                            emp_id = emp['id']
                            a_var = assignments[emp_id][i][shift_a['id']]
                            b_var = assignments[emp_id][j][shift_b['id']]
                            if rest_soft:
                                # both = 1 iff the employee takes both cells.
                                both = model.NewBoolVar(
                                    f'soft_rest_e{emp_id}_{i}_{shift_a["id"]}_{j}_{shift_b["id"]}'
                                )
                                model.Add(both >= a_var + b_var - 1)
                                penalty_terms.append(self.SOFT_LABOR_WEIGHT * both)
                            else:
                                model.Add(a_var + b_var <= 1)

        # --- 4. daily hours (multi-shift days must stay within the cap) ---
        max_daily_minutes = int(round(
            float(constraints.get('max_daily_hours', 8)) * 60
        ))
        daily_soft = 'max_daily_hours' in soft
        for emp in employees:
            emp_id = emp['id']
            for day_idx in range(num_days):
                day_minutes = sum(
                    shift_minutes[s['id']] * assignments[emp_id][day_idx][s['id']]
                    for s in shifts
                )
                if daily_soft:
                    over = model.NewIntVar(
                        0, sum(shift_minutes.values()) + 1,
                        f'soft_daily_e{emp_id}_d{day_idx}',
                    )
                    model.Add(day_minutes - max_daily_minutes <= over)
                    penalty_terms.append(self.SOFT_LABOR_WEIGHT * over)
                else:
                    model.Add(day_minutes <= max_daily_minutes)

        return penalty_terms

    @staticmethod
    def _shift_start_datetime(day: date, shift: Dict[str, Any]) -> datetime:
        start_str = shift.get('start_time', '00:00')
        try:
            t = datetime.strptime(start_str[:5], '%H:%M').time()
        except ValueError:
            t = datetime.min.time()
        return datetime.combine(day, t)

    @staticmethod
    def _shift_end_datetime(day: date, shift: Dict[str, Any]) -> datetime:
        start_str = shift.get('start_time', '00:00')
        end_str = shift.get('end_time', '00:00')
        try:
            start_t = datetime.strptime(start_str[:5], '%H:%M').time()
            end_t = datetime.strptime(end_str[:5], '%H:%M').time()
        except ValueError:
            return datetime.combine(day, datetime.min.time())
        end_dt = datetime.combine(day, end_t)
        # Cross-midnight: end before start → ends on the next calendar day.
        if end_t < start_t:
            end_dt += timedelta(days=1)
        return end_dt

    def _add_drift_objective(
        self,
        model: cp_model.CpModel,
        assignments: Dict,
        employees: List[Dict],
        shifts: List[Dict],
        days: List[date],
        seed: List[Dict[str, Any]],
        today: date,
        time_decay_n: int,
        drift_weight: int,
    ) -> List:
        """
        Cost = drift_weight × Σ over all cells of: time_weight × |result - seed|

        Where:
          - seed cell value is 0 or 1 (1 iff (e,d,s) appears in seed).
          - time_weight = max(1, time_decay_n - |d_to_today|), so cells close
            to today cost more to change. Past-dated cells use abs() so the
            same protection applies in either direction.
          - Because `assignments[e][d][s]` is a 0/1 BoolVar, `|x - seed_val|`
            linearises to `(1 - x)` if seed=1, or `x` if seed=0 — both are
            non-negative, so adding them straight into the objective is safe.

        Returns a list of objective terms (already multiplied by drift_weight
        and time_weight) to be summed into the model's Minimize() call.
        """
        seed_set = set()
        for entry in seed:
            d_raw = entry.get('date')
            d_iso = d_raw if isinstance(d_raw, str) else d_raw.isoformat()
            seed_set.add((entry['employee_id'], d_iso, entry['shift_id']))

        emp_ids = {e['id'] for e in employees}
        shift_ids = {s['id'] for s in shifts}

        terms: List = []
        for day_idx, day in enumerate(days):
            d_iso = day.isoformat()
            time_weight = max(1, time_decay_n - abs((day - today).days))
            cell_coef = drift_weight * time_weight
            for emp in employees:
                emp_id = emp['id']
                for shift in shifts:
                    shift_id = shift['id']
                    var = assignments[emp_id][day_idx][shift_id]
                    in_seed = (emp_id, d_iso, shift_id) in seed_set
                    # diff = var XOR in_seed; linearised below
                    if in_seed:
                        # seed had this cell; cost if result does NOT
                        terms.append(cell_coef * (1 - var))
                    else:
                        # seed lacked this cell; cost if result adds it
                        terms.append(cell_coef * var)

        # Seed cells outside the (emp × shift) cartesian — e.g. an employee
        # who has left, or a retired shift — cannot be preserved, so they
        # contribute a fixed cost regardless of the solver. We ignore them
        # since they cannot influence the search; surfacing them is the
        # caller's responsibility (diff against the produced schedule).
        for (emp_id, d_iso, shift_id) in seed_set:
            if emp_id not in emp_ids or shift_id not in shift_ids:
                continue  # documented above; no-op term
        return terms

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
