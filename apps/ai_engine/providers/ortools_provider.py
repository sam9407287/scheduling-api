"""
OR-Tools Provider Implementation
"""
from datetime import date, datetime, timedelta
from typing import List, Dict, Any
from ortools.sat.python import cp_model
from .base import (
    BaseScheduleProvider,
    ScheduleRequest,
    ScheduleResult,
    ComplianceReport,
    ChangeImpact
)


class ORToolsProvider(BaseScheduleProvider):
    """使用 Google OR-Tools CP-SAT Solver 的內建實作"""
    
    def generate_schedule(self, request: ScheduleRequest) -> ScheduleResult:
        """
        使用 OR-Tools CP-SAT 求解器產生排班表
        """
        try:
            # 建立模型
            model = cp_model.CpModel()
            
            # 準備資料
            employees = request.employees
            shifts = request.shift_templates
            days = self._get_days_in_period(request.period_start, request.period_end)
            
            # 建立決策變數
            # x[employee_id][day][shift_id] = 1 表示該員工在該天被指派該班別
            assignments = {}
            for emp in employees:
                emp_id = emp['id']
                assignments[emp_id] = {}
                for day_idx, day in enumerate(days):
                    assignments[emp_id][day_idx] = {}
                    for shift in shifts:
                        shift_id = shift['id']
                        var_name = f"emp_{emp_id}_day_{day_idx}_shift_{shift_id}"
                        assignments[emp_id][day_idx][shift_id] = model.NewBoolVar(var_name)
            
            # 硬約束
            self._add_hard_constraints(model, assignments, employees, shifts, days, request.constraints)
            
            # 軟約束（目標函數）
            objective_terms = self._add_soft_constraints(
                model, assignments, employees, shifts, days, request.constraints, request.preferences
            )
            
            # 設定目標：最小化違反軟約束的懲罰
            model.Minimize(sum(objective_terms))
            
            # 求解
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 300.0  # 5分鐘超時
            status = solver.Solve(model)
            
            # 處理結果
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
                        'status': 'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE'
                    }
                )
            else:
                return ScheduleResult(
                    success=False,
                    assignments=[],
                    score=float('inf'),
                    violations=[{'type': 'solver_failed', 'message': 'No solution found'}],
                    metadata={
                        'solver': 'OR-Tools CP-SAT',
                        'status': 'INFEASIBLE'
                    },
                    message='無法找到可行解，請檢查約束條件'
                )
        
        except Exception as e:
            return ScheduleResult(
                success=False,
                assignments=[],
                score=float('inf'),
                violations=[{'type': 'error', 'message': str(e)}],
                metadata={'error': str(e)},
                message=f'求解過程發生錯誤: {str(e)}'
            )
    
    def optimize_schedule(self, current_schedule: Dict[str, Any], constraints: Dict[str, Any]) -> ScheduleResult:
        """優化現有排班表"""
        # TODO: 實作優化邏輯
        return ScheduleResult(
            success=False,
            assignments=[],
            score=0.0,
            violations=[],
            metadata={},
            message='尚未實作'
        )
    
    def check_compliance(self, schedule: Dict[str, Any]) -> ComplianceReport:
        """檢查排班表是否合規"""
        violations = []
        warnings = []
        
        # TODO: 實作合規檢查邏輯
        # 檢查每週工時、連續工作天數、休息時間等
        
        return ComplianceReport(
            is_compliant=len(violations) == 0,
            violations=violations,
            warnings=warnings,
            details={}
        )
    
    def evaluate_change(self, schedule: Dict[str, Any], proposed_change: Dict[str, Any]) -> ChangeImpact:
        """評估單一異動的影響"""
        # TODO: 實作影響評估邏輯
        return ChangeImpact(
            can_apply=True,
            impact_score=0.0,
            violations=[],
            warnings=[],
            affected_employees=[]
        )
    
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
        constraints: Dict[str, Any]
    ):
        """加入硬約束"""
        # 1. 每個班別每天至少需要 min_staff_count 人
        for day_idx, day in enumerate(days):
            for shift in shifts:
                shift_id = shift['id']
                min_staff = shift.get('min_staff_count', 1)
                model.Add(
                    sum(
                        assignments[emp['id']][day_idx][shift_id]
                        for emp in employees
                    ) >= min_staff
                )
        
        # 2. 每個員工每天最多只能排一個班別
        for emp in employees:
            emp_id = emp['id']
            for day_idx in range(len(days)):
                model.Add(
                    sum(assignments[emp_id][day_idx].values()) <= 1
                )
        
        # 3. 檢查員工可用性（請假等）
        # TODO: 實作可用性檢查
    
    def _add_soft_constraints(
        self,
        model: cp_model.CpModel,
        assignments: Dict,
        employees: List[Dict],
        shifts: List[Dict],
        days: List[date],
        constraints: Dict[str, Any],
        preferences: Dict[str, Any]
    ) -> List:
        """加入軟約束（目標函數）"""
        objective_terms = []
        
        # 1. 公平分配不受歡迎的班別（夜班等）
        # TODO: 實作公平分配邏輯
        
        # 2. 員工偏好
        # TODO: 實作偏好滿足邏輯
        
        return objective_terms
    
    def _extract_assignments(
        self,
        solver: cp_model.CpSolver,
        assignments: Dict,
        employees: List[Dict],
        shifts: List[Dict],
        days: List[date]
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
