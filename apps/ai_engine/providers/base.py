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
