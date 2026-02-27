"""
Celery tasks for AI scheduling
"""
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from .providers.base import BaseScheduleProvider
import importlib


def get_ai_provider() -> BaseScheduleProvider:
    """取得配置的 AI Provider 實例"""
    provider_path = settings.AI_SCHEDULE_PROVIDER
    module_path, class_name = provider_path.rsplit('.', 1)
    module = importlib.import_module(module_path)
    provider_class = getattr(module, class_name)
    return provider_class()


@shared_task
def generate_schedule_task(request_data: dict):
    """
    非同步產生排班表
    
    Args:
        request_data: ScheduleRequest 的字典格式
        
    Returns:
        ScheduleResult 的字典格式
    """
    from .providers.base import ScheduleRequest
    from datetime import datetime
    
    # 轉換日期字串為 date 物件
    if isinstance(request_data.get('period_start'), str):
        request_data['period_start'] = datetime.fromisoformat(request_data['period_start']).date()
    if isinstance(request_data.get('period_end'), str):
        request_data['period_end'] = datetime.fromisoformat(request_data['period_end']).date()
    
    # 轉換為 ScheduleRequest
    request = ScheduleRequest(**request_data)
    
    # 取得 Provider 並產生排班
    provider = get_ai_provider()
    result = provider.generate_schedule(request)
    
    # 轉換為字典
    return {
        'success': result.success,
        'assignments': result.assignments,
        'score': result.score,
        'violations': result.violations,
        'metadata': result.metadata,
        'message': result.message,
    }
