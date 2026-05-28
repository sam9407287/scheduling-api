"""
Base AI Schedule Provider Interface
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import date


@dataclass
class ScheduleRequest:
    """排班請求"""
    organization_id: int
    branch_id: Optional[int]
    period_start: date
    period_end: date
    employees: List[Dict[str, Any]]  # 可排班員工列表
    shift_templates: List[Dict[str, Any]]  # 可用班別列表
    constraints: Dict[str, Any]  # 硬約束 + 軟約束
    preferences: Dict[str, Any]  # 員工偏好

    # --- Drift / repair mode ---
    # B → A 派生模式 (minimize_drift_from_seed=True) 用：以 seed 為對照，
    # 在滿足所有硬約束的前提下最小化「變更格子數 × 時間遞減權重」。
    # seed 每筆: {employee_id: int, date: 'YYYY-MM-DD', shift_id: int}
    seed: Optional[List[Dict[str, Any]]] = None
    minimize_drift_from_seed: bool = False
    time_decay_n: int = 14
    # `today` 是時間權重的零點；近期格 (d 接近 0) 改動成本最高。
    # 預設 None → ORToolsProvider 內部用 date.today()。
    today: Optional[date] = None
    drift_weight: int = 10  # objective 中 drift 項的乘數

    # --- Team constraints (Notion-filter style 規則) ---
    # 每筆 dict 對應一個 apps.shifts.models.TeamConstraint 列，欄位包含：
    #   id, branch_id?, shift_template_id?, scope_time_of_day,
    #   condition_type, condition_operator, condition_value,
    #   quantifier, quantity, severity, is_active, description
    team_constraints: Optional[List[Dict[str, Any]]] = None

    # --- Labor-law toggle ---
    # True 時把勞基法（週工時、連續工作天數、跨班休息）納入硬約束。
    # 派生 A 模式 (minimize_drift_from_seed=True) 一律強制；純生成模式
    # 預設仍為 False，維持舊路徑回傳行為。
    enforce_labor_law: bool = False

    # --- Soft labour-law rules (PR11) ---
    # Rule types listed here become heavy objective penalties instead of
    # hard constraints, so the solver avoids but won't go INFEASIBLE over
    # them. Valid entries: max_weekly_hours, max_consecutive_days,
    # min_rest_hours. None / empty = all labour-law rules are hard.
    soft_labor_rules: Optional[List[str]] = None


@dataclass
class ScheduleResult:
    """排班結果"""
    success: bool
    assignments: List[Dict[str, Any]]  # [{employee_id, date, shift_id}, ...]
    score: float  # 最佳化分數
    violations: List[Dict[str, Any]]  # 無法滿足的軟約束
    metadata: Dict[str, Any]  # 求解時間、引擎資訊等
    message: Optional[str] = None


@dataclass
class ComplianceReport:
    """合規檢查報告"""
    is_compliant: bool
    violations: List[Dict[str, Any]]
    warnings: List[Dict[str, Any]]
    details: Dict[str, Any]


@dataclass
class ChangeImpact:
    """異動影響評估"""
    can_apply: bool
    impact_score: float
    violations: List[Dict[str, Any]]
    warnings: List[Dict[str, Any]]
    affected_employees: List[int]


class BaseScheduleProvider(ABC):
    """所有 AI 排班引擎必須實作此通用接口"""
    
    @abstractmethod
    def generate_schedule(self, request: ScheduleRequest) -> ScheduleResult:
        """
        根據約束自動產生最佳排班表
        
        Args:
            request: 排班請求
            
        Returns:
            ScheduleResult: 排班結果
        """
        pass
    
    @abstractmethod
    def optimize_schedule(self, current_schedule: Dict[str, Any], constraints: Dict[str, Any]) -> ScheduleResult:
        """
        優化現有排班表
        
        Args:
            current_schedule: 現有排班表
            constraints: 約束條件
            
        Returns:
            ScheduleResult: 優化後的排班結果
        """
        pass
    
    @abstractmethod
    def check_compliance(self, schedule: Dict[str, Any]) -> ComplianceReport:
        """
        檢查排班表是否合規
        
        Args:
            schedule: 排班表資料
            
        Returns:
            ComplianceReport: 合規檢查報告
        """
        pass
    
    @abstractmethod
    def evaluate_change(self, schedule: Dict[str, Any], proposed_change: Dict[str, Any]) -> ChangeImpact:
        """
        評估單一異動（代班/拆班）的影響
        
        Args:
            schedule: 現有排班表
            proposed_change: 提議的異動
            
        Returns:
            ChangeImpact: 影響評估
        """
        pass
