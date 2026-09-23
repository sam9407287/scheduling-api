"""
AI Engine views
"""
from datetime import date as _date
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.conf import settings
import importlib
from .serializers import ScheduleRequestSerializer, ScheduleResultSerializer
from .providers.base import BaseScheduleProvider, ScheduleRequest
from .tasks import generate_schedule_task
from apps.accounts.permissions import IsManager


def _employee_attributes_for_solver(emp) -> dict:
    """
    Build the 'attributes' dict the team-constraint compiler reads.

    Sensitive numeric/categorical values go through
    `sensitive_attributes_for_solver()` which already collapses them to None
    when the employee has no active EmployeeDataConsent. age_years is derived
    from birth_date (also gated by consent because birth_date itself is).
    Non-sensitive sets — tags and certification ids — are always exposed.
    """
    sensitive = emp.sensitive_attributes_for_solver()  # gender/birth_date/h/w
    birth = sensitive.get('birth_date')
    if birth:
        today = _date.today()
        age = today.year - birth.year - (
            (today.month, today.day) < (birth.month, birth.day)
        )
    else:
        age = None
    return {
        'gender': sensitive.get('gender'),
        'height_cm': float(sensitive['height_cm']) if sensitive.get('height_cm') is not None else None,
        'weight_kg': float(sensitive['weight_kg']) if sensitive.get('weight_kg') is not None else None,
        'age_years': age,
        'tag_codes': list(emp.tags.values_list('code', flat=True)),
        'certification_ids': list(emp.certifications.values_list('id', flat=True)),
        # Non-sensitive: the employee themselves picks this preference in
        # the availability UI, so it is not gated by EmployeeDataConsent.
        'shift_pattern_preference': emp.shift_pattern_preference,
    }


def _load_team_constraints(organization_id: int, branch_id=None) -> list:
    """Serialise active TeamConstraint rows scoped to org (+ optional branch)."""
    from apps.shifts.models import TeamConstraint
    qs = TeamConstraint.objects.filter(
        organization_id=organization_id, is_active=True
    )
    # branch_id filter happens inside the compiler so an org-wide constraint
    # (branch_id=None) still applies; we only need to fetch everything here.
    return [
        {
            'id': tc.id,
            'branch_id': tc.branch_id,
            'shift_template_id': tc.shift_template_id,
            'scope_time_of_day': tc.scope_time_of_day,
            'condition_type': tc.condition_type,
            'condition_operator': tc.condition_operator,
            'condition_value': tc.condition_value,
            'quantifier': tc.quantifier,
            'quantity': tc.quantity,
            'severity': tc.severity,
            'is_active': tc.is_active,
            'description': tc.description or '',
        }
        for tc in qs
    ]


def get_ai_provider() -> BaseScheduleProvider:
    """取得配置的 AI Provider 實例"""
    provider_path = settings.AI_SCHEDULE_PROVIDER
    module_path, class_name = provider_path.rsplit('.', 1)
    module = importlib.import_module(module_path)
    provider_class = getattr(module, class_name)
    return provider_class()


def _load_schedule_data(schedule_version):
    """
    將 ScheduleVersion DB 物件轉為 AI provider 所需的 dict 格式。
    """
    from apps.schedules.models import Schedule
    from apps.shifts.models import ShiftTemplate
    from apps.employees.models import Employee

    schedules = (
        Schedule.objects
        .filter(schedule_version=schedule_version)
        .select_related('shift_template', 'employee')
    )

    assignments = []
    shift_ids = set()
    emp_ids = set()

    for s in schedules:
        assignments.append({
            'employee_id': s.employee_id,
            'date': s.schedule_date.isoformat(),
            'shift_id': s.shift_template_id,
            'shift_name': s.shift_template.name,
        })
        shift_ids.add(s.shift_template_id)
        emp_ids.add(s.employee_id)

    shift_templates = [
        {
            'id': st.id,
            'name': st.name,
            'start_time': st.start_time.strftime('%H:%M'),
            'end_time': st.end_time.strftime('%H:%M'),
            'break_minutes': st.break_minutes,
        }
        for st in ShiftTemplate.objects.filter(id__in=shift_ids)
    ]

    employees = [
        {
            'id': emp.id,
            'employee_id': emp.employee_id,
            'agreed_hours_per_week': float(emp.agreed_hours_per_week),
        }
        for emp in Employee.objects.filter(id__in=emp_ids)
    ]

    return {
        'assignments': assignments,
        'employees': employees,
        'shift_templates': shift_templates,
        'constraints': {
            'max_weekly_hours': 40,
            'min_rest_hours': 11,
            'max_consecutive_days': 6,
        },
    }


class AIEngineViewSet(viewsets.ViewSet):
    """AI 排班引擎 API"""
    permission_classes = [IsManager]

    @action(detail=False, methods=['post'])
    def generate(self, request):
        """
        產生排班表。

        request body:
          organization_id, branch_id?, period_start, period_end,
          employee_ids?, shift_template_ids?,
          constraints?, preferences?,
          run_async (bool, default false)
        """
        serializer = ScheduleRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        org_id = data['organization_id']
        branch_id = data.get('branch_id')
        period_start = data['period_start']
        period_end = data['period_end']

        # 取得員工
        from apps.employees.models import Employee
        from apps.schedules.models import Schedule as ScheduleModel

        employees_qs = Employee.objects.filter(
            organization_id=org_id, is_active=True
        ).prefetch_related('certifications')
        if branch_id:
            employees_qs = employees_qs.filter(branch_id=branch_id)
        if data.get('employee_ids'):
            employees_qs = employees_qs.filter(id__in=data['employee_ids'])

        # 建立員工不可用日期 map（來自其他已確認/完成版本的排班）
        employee_id_list = list(employees_qs.values_list('id', flat=True))
        unavailability_map: dict = {eid: [] for eid in employee_id_list}

        confirmed_schedules = (
            ScheduleModel.objects
            .filter(
                employee_id__in=employee_id_list,
                schedule_date__gte=period_start,
                schedule_date__lte=period_end,
                status__in=['confirmed', 'completed'],
            )
            .exclude(schedule_version__status='draft')
            .values('employee_id', 'schedule_date')
        )
        for s in confirmed_schedules:
            unavailability_map[s['employee_id']].append(s['schedule_date'].isoformat())

        # 已核准請假日＝solver 硬性不可排班（手動排班仍僅警告不阻擋）
        from apps.leaves.solver_dates import approved_leave_dates, approved_leave_intervals
        for eid, dates in approved_leave_dates(
            employee_id_list, period_start, period_end
        ).items():
            unavailability_map[eid].extend(dates)
        # 時段假：只擋重疊班別
        leave_intervals = approved_leave_intervals(
            employee_id_list, period_start, period_end)

        # 合併呼叫方手動傳入的不可用日期
        manual_unavailability: dict = data.get('constraints', {}).get('employee_unavailability', {})

        employees = []
        for emp in employees_qs:
            # 可用性設定（blocked/preferred slots + required hours + special_rules）
            avail_data: dict = {}
            try:
                avail = emp.availability
                avail_data = {
                    'required_hours_per_week': (
                        float(avail.required_hours_per_week)
                        if avail.required_hours_per_week is not None else None
                    ),
                    'special_rules': avail.special_rules or '',
                    'blocked_slots': [
                        {
                            'day_of_week': s.day_of_week,
                            'start_time': s.start_time.strftime('%H:%M'),
                            'end_time': s.end_time.strftime('%H:%M'),
                        }
                        for s in avail.time_slots.filter(slot_type='blocked')
                    ],
                    'preferred_slots': [
                        {
                            'day_of_week': s.day_of_week,
                            'start_time': s.start_time.strftime('%H:%M'),
                            'end_time': s.end_time.strftime('%H:%M'),
                        }
                        for s in avail.time_slots.filter(slot_type='preferred')
                    ],
                }
            except Exception:
                pass  # 員工尚未設定可用性，視為無限制

            employees.append({
                'id': emp.id,
                'employee_id': emp.employee_id,
                'agreed_hours_per_week': float(emp.agreed_hours_per_week),
                'certifications': [c.id for c in emp.certifications.all()],
                'unavailable_dates': list(set(
                    unavailability_map.get(emp.id, [])
                    + manual_unavailability.get(str(emp.id), [])
                )),
                'unavailable_intervals': leave_intervals.get(emp.id, {}),
                'availability': avail_data,
                # Attributes consumed by the team-constraint compiler.
                # Sensitive fields here are auto-null-gated by EmployeeDataConsent.
                'attributes': _employee_attributes_for_solver(emp),
            })

        # 取得班別
        from apps.shifts.models import ShiftTemplate
        shifts_qs = ShiftTemplate.objects.filter(
            organization_id=org_id, is_active=True
        ).prefetch_related('required_certifications', 'employee_priorities')
        if data.get('shift_template_ids'):
            shifts_qs = shifts_qs.filter(id__in=data['shift_template_ids'])

        shifts = [
            {
                'id': shift.id,
                'name': shift.name,
                'start_time': shift.start_time.isoformat(),
                'end_time': shift.end_time.isoformat(),
                'break_minutes': shift.break_minutes,
                'min_staff_count': shift.min_staff_count,
                'required_certifications': [c.id for c in shift.required_certifications.all()],
                'employee_priorities': [
                    {
                        'employee_id': p.employee_id,
                        'priority_rank': p.priority_rank,
                        'max_extra_shifts': p.max_extra_shifts,
                    }
                    for p in shift.employee_priorities.all()
                ],
            }
            for shift in shifts_qs
        ]

        # ---- seed: 載入指定 ScheduleVersion 的 schedule rows ----
        seed = None
        seed_version_id = data.get('seed_version_id')
        if seed_version_id:
            from apps.schedules.models import Schedule as ScheduleModel, ScheduleVersion
            try:
                seed_version = ScheduleVersion.objects.get(id=seed_version_id)
            except ScheduleVersion.DoesNotExist:
                return Response(
                    {'error': f'seed_version_id {seed_version_id} not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if (not request.user.is_superuser
                    and seed_version.organization_id != org_id):
                return Response(
                    {'error': 'seed version belongs to a different organization'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            seed = [
                {
                    'employee_id': s.employee_id,
                    'date': s.schedule_date.isoformat(),
                    'shift_id': s.shift_template_id,
                }
                for s in ScheduleModel.objects.filter(schedule_version_id=seed_version_id)
            ]

        team_constraints = _load_team_constraints(org_id, branch_id)

        # 優先序：solver 預設 8 < org ShiftRule < request constraints
        from apps.shifts.rules import resolve_max_daily_hours
        request_constraints = dict(data.get('constraints') or {})
        if 'max_daily_hours' not in request_constraints:
            org_daily_cap = resolve_max_daily_hours(org_id)
            if org_daily_cap is not None:
                request_constraints['max_daily_hours'] = org_daily_cap

        # Soft labour-law rules (PR11): caller override else org config.
        from apps.compliance.models import OrgComplianceSettings
        cfg = OrgComplianceSettings.objects.filter(organization_id=org_id).first()
        soft_labor_rules = data.get('soft_rule_types')
        if soft_labor_rules is None:
            soft_labor_rules = cfg.soft_rule_types if cfg else []
        # 機構每週公休日（PM#1）：solver 硬性避開
        if 'closed_weekdays' not in request_constraints:
            request_constraints['closed_weekdays'] = (
                cfg.weekly_closed_days if cfg else []
            )

        schedule_request = ScheduleRequest(
            organization_id=org_id,
            branch_id=branch_id,
            period_start=period_start,
            period_end=period_end,
            employees=employees,
            shift_templates=shifts,
            constraints=request_constraints,
            preferences=data.get('preferences', {}),
            seed=seed,
            minimize_drift_from_seed=bool(data.get('minimize_drift_from_seed')),
            time_decay_n=int(data.get('time_decay_n', 14)),
            today=data.get('today'),
            drift_weight=int(data.get('drift_weight', 10)),
            team_constraints=team_constraints,
            enforce_labor_law=bool(data.get('enforce_labor_law')),
            soft_labor_rules=soft_labor_rules,
        )

        # Classify the request into one of the three metered modes. The seed-
        # density heuristic distinguishes "AI 補齊" (sparse seed) from
        # "派生 A" (dense seed) so the customer sees a fair price preview.
        billing_mode = (
            'derive_legal' if (seed and data.get('minimize_drift_from_seed')
                               and len(seed) >= max(1, int(0.5 * len(employees) * 7)))
            else 'fill_gaps' if seed and data.get('minimize_drift_from_seed')
            else 'generate'
        )
        consume_token = bool(data.get('consume_token', True))
        billing_metadata = {
            'consume_token': consume_token,
            'billing_mode': billing_mode,
            'enforce_labor_law': bool(data.get('enforce_labor_law')),
        }

        # ---- Pre-flight monthly cap check (PR8) -------------------------
        from apps.organizations.models import Organization
        from apps.billing.models import (
            would_exceed_cap, record_usage, OrgBillingSettings,
            estimate_tokens,
        )
        try:
            org_obj = Organization.objects.get(id=org_id)
        except Organization.DoesNotExist:
            return Response(
                {'error': f'organization {org_id} not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Allow callers to opt out (consume_token=false) for dry runs;
        # otherwise check both the kill-switch and the monthly cap *before*
        # spinning up the solver — solver runs are expensive, denying early
        # avoids wasting solve_time on customers who will be 402'd anyway.
        if consume_token:
            org_settings = OrgBillingSettings.objects.filter(
                organization=org_obj
            ).first()
            if org_settings and not org_settings.is_billing_enabled:
                return Response(
                    {'error': 'billing is disabled for this organization',
                     'billing_mode': billing_mode},
                    status=status.HTTP_402_PAYMENT_REQUIRED,
                )
            exceeds, current, projected, cap = would_exceed_cap(
                org_obj, billing_mode,
            )
            if exceeds:
                return Response({
                    'error': 'monthly billing cap exceeded',
                    'billing_mode': billing_mode,
                    'tokens_required': estimate_tokens(billing_mode),
                    'current_period_tokens': current,
                    'projected_period_tokens': projected,
                    'monthly_cap_tokens': cap,
                }, status=status.HTTP_402_PAYMENT_REQUIRED)

        if data.get('run_async', False):
            task = generate_schedule_task.delay({
                'organization_id': schedule_request.organization_id,
                'branch_id': schedule_request.branch_id,
                'period_start': schedule_request.period_start.isoformat(),
                'period_end': schedule_request.period_end.isoformat(),
                'employees': schedule_request.employees,
                'shift_templates': schedule_request.shift_templates,
                'constraints': schedule_request.constraints,
                'preferences': schedule_request.preferences,
                'seed': schedule_request.seed,
                'minimize_drift_from_seed': schedule_request.minimize_drift_from_seed,
                'time_decay_n': schedule_request.time_decay_n,
                'today': (schedule_request.today.isoformat()
                          if schedule_request.today else None),
                'drift_weight': schedule_request.drift_weight,
                'team_constraints': schedule_request.team_constraints,
                'enforce_labor_law': schedule_request.enforce_labor_law,
                'soft_labor_rules': schedule_request.soft_labor_rules,
                # Billing hand-off — task records usage after the solver.
                # Cap is already pre-checked here; the task only writes.
                '_billing': {
                    'mode': billing_mode,
                    'consume_token': consume_token,
                    'org_id': org_obj.id,
                    'user_id': request.user.id if request.user.is_authenticated else None,
                    'period_start': period_start.isoformat(),
                    'period_end': period_end.isoformat(),
                    'employee_count': len(employees),
                    'shift_count': len(shifts),
                },
            })
            return Response(
                {'task_id': task.id, 'status': 'pending',
                 'message': '排班任務已提交，請稍後查詢結果',
                 'billing': billing_metadata},
                status=status.HTTP_202_ACCEPTED,
            )

        provider = get_ai_provider()
        result = provider.generate_schedule(schedule_request)

        # ---- Post-debit (PR8) -----------------------------------------
        # Pre-debit per the customer rule (先扱不退): we record usage on
        # success AND on INFEASIBLE/error. Callers can suppress by setting
        # consume_token=false (dry-run / internal use).
        if consume_token:
            if result.success:
                solver_status_for_billing = 'success'
            elif any(v.get('type') == 'error' for v in (result.violations or [])):
                solver_status_for_billing = 'error'
            else:
                solver_status_for_billing = 'infeasible'
            usage = record_usage(
                organization=org_obj,
                billing_mode=billing_mode,
                solver_status=solver_status_for_billing,
                user=request.user if request.user.is_authenticated else None,
                schedule_version=None,
                request_metadata={
                    'period_start': period_start.isoformat(),
                    'period_end': period_end.isoformat(),
                    'employee_count': len(employees),
                    'shift_count': len(shifts),
                },
            )
            billing_metadata['tokens_charged'] = usage.tokens_charged
            billing_metadata['period_usage_after'] = usage.billing_period.total_tokens

        result.metadata = {**(result.metadata or {}), 'billing': billing_metadata}
        return Response(ScheduleResultSerializer(result).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def optimize(self, request):
        """
        優化現有排班版本。

        request body:
          schedule_version_id (int),
          constraints? (dict, 可覆蓋預設規則),
          run_async (bool, default false)
        """
        from apps.schedules.models import ScheduleVersion

        version_id = request.data.get('schedule_version_id')
        if not version_id:
            return Response(
                {'error': 'schedule_version_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            schedule_version = ScheduleVersion.objects.get(id=version_id)
        except ScheduleVersion.DoesNotExist:
            return Response({'error': 'Schedule version not found'}, status=status.HTTP_404_NOT_FOUND)

        if not request.user.is_superuser and schedule_version.organization != request.user.organization:
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

        current_schedule = _load_schedule_data(schedule_version)
        extra_constraints = request.data.get('constraints', {})
        constraints = {
            **current_schedule['constraints'],
            **extra_constraints,
            'period_start': schedule_version.period_start.isoformat(),
            'period_end': schedule_version.period_end.isoformat(),
            'organization_id': schedule_version.organization_id,
            'branch_id': schedule_version.branch_id,
        }

        provider = get_ai_provider()
        result = provider.optimize_schedule(current_schedule, constraints)
        return Response(ScheduleResultSerializer(result).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def check_compliance(self, request):
        """
        以 AI provider 檢查排班版本的勞基法合規性。

        request body: { schedule_version_id: int, constraints?: dict }
        """
        from apps.schedules.models import ScheduleVersion

        version_id = request.data.get('schedule_version_id')
        if not version_id:
            return Response(
                {'error': 'schedule_version_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            schedule_version = ScheduleVersion.objects.get(id=version_id)
        except ScheduleVersion.DoesNotExist:
            return Response({'error': 'Schedule version not found'}, status=status.HTTP_404_NOT_FOUND)

        if not request.user.is_superuser and schedule_version.organization != request.user.organization:
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

        schedule_data = _load_schedule_data(schedule_version)

        # 允許呼叫方覆蓋預設規則
        extra_constraints = request.data.get('constraints', {})
        if extra_constraints:
            schedule_data['constraints'].update(extra_constraints)

        provider = get_ai_provider()
        report = provider.check_compliance(schedule_data)

        return Response(
            {
                'is_compliant': report.is_compliant,
                'violations': report.violations,
                'warnings': report.warnings,
                'details': report.details,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'])
    def evaluate_change(self, request):
        """
        評估排班異動的合規影響。

        request body:
          schedule_version_id (int),
          proposed_change (dict):
            type: 'substitute' | 'cancel' | 'modify'
            employee_id: <db id>
            date: 'YYYY-MM-DD'
            shift_id: <班別 db id>
            new_employee_id: <db id>    (substitute 必填)
            new_shift_id: <db id>       (modify 選填)
            new_date: 'YYYY-MM-DD'      (modify 選填)
        """
        from apps.schedules.models import ScheduleVersion

        version_id = request.data.get('schedule_version_id')
        proposed_change = request.data.get('proposed_change')

        if not version_id or not proposed_change:
            return Response(
                {'error': 'schedule_version_id and proposed_change are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            schedule_version = ScheduleVersion.objects.get(id=version_id)
        except ScheduleVersion.DoesNotExist:
            return Response({'error': 'Schedule version not found'}, status=status.HTTP_404_NOT_FOUND)

        if not request.user.is_superuser and schedule_version.organization != request.user.organization:
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

        schedule_data = _load_schedule_data(schedule_version)

        provider = get_ai_provider()
        impact = provider.evaluate_change(schedule_data, proposed_change)

        return Response(
            {
                'can_apply': impact.can_apply,
                'impact_score': impact.impact_score,
                'violations': impact.violations,
                'warnings': impact.warnings,
                'affected_employees': impact.affected_employees,
            },
            status=status.HTTP_200_OK,
        )


class LLMScheduleViewSet(viewsets.ViewSet):
    """純 LLM 排班（2026-09-23 產品決策）：免費模型直接產生班表。

    前端「AI 排班請求」按鈕唯一要打的端點——排完直接寫入版本，
    前端 refetch 班表即可看到格子。品質由驗證層守住：模型輸出的
    每一筆都比對現實，不合格丟棄並回報，絕不寫入髒資料。
    """
    permission_classes = [IsManager]

    @action(detail=False, methods=['post'], url_path='llm-generate')
    def llm_generate(self, request):
        from django.utils.dateparse import parse_date
        from decimal import Decimal
        from apps.schedules.models import Schedule, ScheduleVersion
        from apps.billing.models import (
            OrgBillingSettings, estimate_tokens, record_usage, would_exceed_cap,
        )
        from . import llm_provider, llm_scheduler

        version_id = request.data.get('schedule_version')
        if not version_id:
            return Response({'error': 'schedule_version is required'},
                            status=status.HTTP_400_BAD_REQUEST)

        versions = ScheduleVersion.objects.all()
        if not request.user.is_superuser:
            versions = versions.filter(organization=request.user.organization)
        version = versions.filter(pk=version_id).first()
        if version is None:
            return Response({'error': 'schedule version not found'},
                            status=status.HTTP_404_NOT_FOUND)
        if version.status != 'draft':
            return Response(
                {'code': 'schedule_version_locked',
                 'error': 'Approved schedule versions are read-only.'},
                status=status.HTTP_409_CONFLICT)

        period_start = parse_date(request.data.get('period_start') or '') or version.period_start
        period_end = parse_date(request.data.get('period_end') or '') or version.period_end
        if period_end < period_start or (period_end - period_start).days > 62:
            return Response({'error': 'invalid period (max 62 days)'},
                            status=status.HTTP_400_BAD_REQUEST)

        # 計費：LLM 排班比照 generate 模式（產品決策：所有 AI 動作收費）
        consume_token = bool(request.data.get('consume_token', True))
        org = version.organization
        if consume_token:
            settings_row = OrgBillingSettings.objects.filter(organization=org).first()
            if settings_row and not settings_row.is_billing_enabled:
                return Response({'error': 'billing is disabled for this organization'},
                                status=status.HTTP_402_PAYMENT_REQUIRED)
            exceeds, current, projected, cap = would_exceed_cap(org, 'generate')
            if exceeds:
                return Response({
                    'error': 'monthly billing cap exceeded',
                    'tokens_required': estimate_tokens('generate'),
                    'current_period_tokens': current, 'monthly_cap_tokens': cap,
                }, status=status.HTTP_402_PAYMENT_REQUIRED)

        context, lookups = llm_scheduler.build_context(version, period_start, period_end)
        if not context['employees'] or not context['shifts']:
            return Response({'error': '此機構沒有可排班的員工或班別'},
                            status=status.HTTP_400_BAD_REQUEST)

        import json as _json
        try:
            output, model_name = llm_provider.generate_json(
                llm_scheduler.SYSTEM_PROMPT,
                _json.dumps(context, ensure_ascii=False),
            )
        except llm_provider.LLMNotConfigured as exc:
            return Response({'code': 'llm_not_configured', 'error': str(exc)},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except llm_provider.LLMCallError as exc:
            return Response({'code': 'llm_call_failed', 'error': str(exc)},
                            status=status.HTTP_502_BAD_GATEWAY)

        raw_assignments = output.get('assignments') if isinstance(output, dict) else output
        valid, rejected = llm_scheduler.validate_assignments(
            raw_assignments or [], lookups, version)
        warnings = llm_scheduler.coverage_warnings(valid, lookups)

        created = []
        for row in valid:
            schedule = Schedule.objects.create(
                schedule_version=version,
                employee=row['employee'],
                shift_template=row['shift'],
                schedule_date=row['date'],
                expected_hours=Decimal(str(row['shift'].duration_hours)),
                status='assigned',
                notes='AI 排班',
            )
            created.append({
                'id': schedule.pk,
                'employee_id': row['employee'].pk,
                'date': row['date'],
                'shift_id': row['shift'].pk,
            })
        # 版本期間只擴不縮（與手動排班一致）
        if created:
            dates = sorted(r['date'] for r in created)
            ScheduleVersion.objects.filter(
                pk=version.pk, period_start__gt=dates[0]).update(period_start=dates[0])
            ScheduleVersion.objects.filter(
                pk=version.pk, period_end__lt=dates[-1]).update(period_end=dates[-1])

        billing_info = None
        if consume_token:
            usage = record_usage(
                organization=org, billing_mode='generate',
                solver_status='SUCCESS' if created else 'EMPTY',
                user=request.user if request.user.is_authenticated else None,
                schedule_version=version,
                request_metadata={
                    'engine': 'llm', 'model': model_name,
                    'period_start': period_start.isoformat(),
                    'period_end': period_end.isoformat(),
                },
            )
            billing_info = {'billing_mode': 'generate',
                            'tokens_charged': usage.tokens_charged}

        return Response({
            'created_count': len(created),
            'rejected_count': len(rejected),
            'assignments': created,
            'rejected': rejected[:50],
            'warnings': warnings[:50],
            'model': model_name,
            'engine': 'llm',
            'billing': billing_info,
        }, status=status.HTTP_201_CREATED)
