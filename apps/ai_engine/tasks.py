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
    Asynchronous schedule generation.

    `request_data['_billing']` is a side-channel containing the billing
    intent that the view already pre-flight-checked against the monthly
    cap. The task only *records* the usage after solving; the cap check
    is not repeated here because the cap could have shifted between view
    enqueue and worker pickup, and we don't want to silently 402 a job
    that the customer already saw accepted.
    """
    from .providers.base import ScheduleRequest
    from datetime import datetime

    # 轉換日期字串為 date 物件
    if isinstance(request_data.get('period_start'), str):
        request_data['period_start'] = datetime.fromisoformat(request_data['period_start']).date()
    if isinstance(request_data.get('period_end'), str):
        request_data['period_end'] = datetime.fromisoformat(request_data['period_end']).date()
    if isinstance(request_data.get('today'), str):
        request_data['today'] = datetime.fromisoformat(request_data['today']).date()

    billing_intent = request_data.pop('_billing', None)

    # 轉換為 ScheduleRequest
    request = ScheduleRequest(**request_data)

    # 取得 Provider 並產生排班
    provider = get_ai_provider()
    result = provider.generate_schedule(request)

    # Pre-debit usage record. Skipped when consume_token=false on the
    # original view call (e.g. tests, internal dry-runs).
    billing_payload = None
    if billing_intent and billing_intent.get('consume_token'):
        from apps.billing.models import record_usage
        from apps.organizations.models import Organization
        from apps.accounts.models import User
        try:
            org = Organization.objects.get(id=billing_intent['org_id'])
        except Organization.DoesNotExist:
            org = None
        user = None
        if billing_intent.get('user_id'):
            user = User.objects.filter(id=billing_intent['user_id']).first()
        if org is not None:
            if result.success:
                solver_status = 'success'
            elif any(v.get('type') == 'error' for v in (result.violations or [])):
                solver_status = 'error'
            else:
                solver_status = 'infeasible'
            usage = record_usage(
                organization=org,
                billing_mode=billing_intent['mode'],
                solver_status=solver_status,
                user=user,
                request_metadata={
                    'period_start': billing_intent.get('period_start'),
                    'period_end': billing_intent.get('period_end'),
                    'employee_count': billing_intent.get('employee_count'),
                    'shift_count': billing_intent.get('shift_count'),
                    'async': True,
                },
            )
            billing_payload = {
                'consume_token': True,
                'billing_mode': billing_intent['mode'],
                'tokens_charged': usage.tokens_charged,
                'period_usage_after': usage.billing_period.total_tokens,
            }

    metadata = dict(result.metadata or {})
    if billing_payload:
        metadata['billing'] = billing_payload

    return {
        'success': result.success,
        'assignments': result.assignments,
        'score': result.score,
        'violations': result.violations,
        'metadata': metadata,
        'message': result.message,
    }
