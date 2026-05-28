"""
Schedule views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import Schedule, ScheduleVersion, ScheduleChange
from .serializers import (
    ScheduleSerializer,
    ScheduleVersionSerializer,
    ScheduleChangeSerializer
)
from apps.accounts.permissions import IsManager, IsSupervisor


class ScheduleVersionViewSet(viewsets.ModelViewSet):
    """排班版本管理"""
    queryset = ScheduleVersion.objects.select_related('organization', 'branch', 'approved_by', 'created_by').prefetch_related('schedules')
    serializer_class = ScheduleVersionSerializer
    permission_classes = [IsSupervisor]
    search_fields = ['version_label', 'organization__name']
    ordering_fields = ['-period_start', '-created_at']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by organization
        org_id = self.request.query_params.get('organization')
        if org_id:
            queryset = queryset.filter(organization_id=org_id)
        
        # Filter by version_type
        version_type = self.request.query_params.get('version_type')
        if version_type:
            queryset = queryset.filter(version_type=version_type)
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset
    
    @action(detail=True, methods=['post'], url_path='derive-legal')
    def derive_legal(self, request, pk=None):
        """
        從 B (actual) 派生 A (legal)：在滿足所有勞基法硬約束的前提下，
        最小化「變更格子數 × 距今時間遞減權重」。

        Request body (optional):
          - today:        'YYYY-MM-DD'  時間權重的零點 (default: server today)
          - time_decay_n: int           權重 N (default: 14)
          - drift_weight: int           drift cost 乘數 (default: 10)
          - constraints:  dict          覆蓋預設勞基法值 (max_weekly_hours, …)
          - label:        str           新版本 label (default: "<B-label> (legal)")
        """
        from datetime import date as _date
        from django.db import transaction
        from django.utils.dateparse import parse_date
        from .models import Schedule
        from apps.shifts.models import ShiftTemplate
        from apps.employees.models import Employee
        from apps.ai_engine.providers.base import ScheduleRequest
        from apps.ai_engine.views import get_ai_provider
        from apps.billing.models import (
            would_exceed_cap, record_usage, estimate_tokens,
            OrgBillingSettings,
        )

        b_version = self.get_object()
        if b_version.version_type != 'actual':
            return Response(
                {'error': 'derive-legal must be invoked on an actual (B) version'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        body = request.data or {}
        consume_token = bool(body.get('consume_token', True))

        # Pre-flight monthly cap check. derive-legal is always billing_mode
        # 'derive_legal' regardless of seed density (this is the explicit
        # "produce A from B" button on the frontend).
        if consume_token:
            org_settings = OrgBillingSettings.objects.filter(
                organization=b_version.organization
            ).first()
            if org_settings and not org_settings.is_billing_enabled:
                return Response(
                    {'error': 'billing is disabled for this organization',
                     'billing_mode': 'derive_legal'},
                    status=status.HTTP_402_PAYMENT_REQUIRED,
                )
            exceeds, current, projected, cap = would_exceed_cap(
                b_version.organization, 'derive_legal',
            )
            if exceeds:
                return Response({
                    'error': 'monthly billing cap exceeded',
                    'billing_mode': 'derive_legal',
                    'tokens_required': estimate_tokens('derive_legal'),
                    'current_period_tokens': current,
                    'projected_period_tokens': projected,
                    'monthly_cap_tokens': cap,
                }, status=status.HTTP_402_PAYMENT_REQUIRED)
        today_raw = body.get('today')
        today = parse_date(today_raw) if today_raw else None
        if today_raw and not today:
            return Response(
                {'error': f'invalid `today` date: {today_raw!r}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        time_decay_n = int(body.get('time_decay_n', 14))
        drift_weight = int(body.get('drift_weight', 10))
        constraints_override = dict(body.get('constraints') or {})

        # 1) 載入 B 的所有 schedule rows → seed
        b_schedules = list(
            Schedule.objects
            .filter(schedule_version=b_version)
            .select_related('shift_template', 'employee')
        )
        if not b_schedules:
            return Response(
                {'error': 'B version has no schedule rows to derive from'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        seed = [
            {
                'employee_id': s.employee_id,
                'date': s.schedule_date.isoformat(),
                'shift_id': s.shift_template_id,
            }
            for s in b_schedules
        ]

        # 2) 求解空間 = B 涉及的員工 ∪ 同 org/branch 內所有 active 員工。
        # 修補 B 時常需要把違規員工的班次轉移給未排到的同事，否則 min_staff
        # 將無法滿足（這實務上是必要的：B 用了 E1 七天，cap=6，必須由 E2 接手）。
        b_emp_ids = {s.employee_id for s in b_schedules}
        shift_ids = {s.shift_template_id for s in b_schedules}

        candidates_qs = (
            Employee.objects
            .filter(organization=b_version.organization, is_active=True)
        )
        if b_version.branch_id:
            # 限縮到同分店；若 B 跨分店則前端應在派生前手動拆 B。
            candidates_qs = candidates_qs.filter(branch_id=b_version.branch_id)
        # 一律包含 B 涉及的員工（即使他們可能已停用）。
        candidates_qs = (
            Employee.objects
            .filter(
                pk__in=set(candidates_qs.values_list('pk', flat=True)) | b_emp_ids
            )
            .prefetch_related('certifications')
        )

        employees = [
            {
                'id': emp.id,
                'employee_id': emp.employee_id,
                'agreed_hours_per_week': float(emp.agreed_hours_per_week),
                'certifications': list(
                    emp.certifications.values_list('id', flat=True)
                ),
                'unavailable_dates': [],
                'availability': {},
            }
            for emp in candidates_qs
        ]
        shifts = [
            {
                'id': st.id,
                'name': st.name,
                'start_time': st.start_time.isoformat(),
                'end_time': st.end_time.isoformat(),
                'break_minutes': st.break_minutes,
                'min_staff_count': st.min_staff_count,
                'required_certifications': list(
                    st.required_certifications.values_list('id', flat=True)
                ),
                'employee_priorities': [],
            }
            for st in ShiftTemplate.objects
                .filter(id__in=shift_ids)
                .prefetch_related('required_certifications')
        ]

        labor_law_defaults = {
            'max_weekly_hours': 40,
            'min_rest_hours': 11,
            'max_consecutive_days': 6,
        }
        labor_law_defaults.update(constraints_override)

        # Soft labour-law rules (PR11): caller override else org config.
        from apps.compliance.models import OrgComplianceSettings
        soft_labor_rules = body.get('soft_rule_types')
        if soft_labor_rules is None:
            cfg = OrgComplianceSettings.objects.filter(
                organization=b_version.organization
            ).first()
            soft_labor_rules = cfg.soft_rule_types if cfg else []

        schedule_request = ScheduleRequest(
            organization_id=b_version.organization_id,
            branch_id=b_version.branch_id,
            period_start=b_version.period_start,
            period_end=b_version.period_end,
            employees=employees,
            shift_templates=shifts,
            constraints=labor_law_defaults,
            preferences={},
            seed=seed,
            minimize_drift_from_seed=True,
            time_decay_n=time_decay_n,
            today=today,
            drift_weight=drift_weight,
            soft_labor_rules=soft_labor_rules,
        )

        provider = get_ai_provider()
        result = provider.generate_schedule(schedule_request)
        if not result.success:
            # Pre-debit: failed solves still incur a charge. Customer rule
            # is "先扱不退" — an INFEASIBLE result still uses solver time.
            billing_payload = None
            if consume_token:
                solver_status = (
                    'error' if any(v.get('type') == 'error'
                                   for v in (result.violations or []))
                    else 'infeasible'
                )
                usage = record_usage(
                    organization=b_version.organization,
                    billing_mode='derive_legal',
                    solver_status=solver_status,
                    user=request.user if request.user.is_authenticated else None,
                    schedule_version=b_version,
                    request_metadata={'derived_from_id': b_version.id},
                )
                billing_payload = {
                    'tokens_charged': usage.tokens_charged,
                    'period_usage_after': usage.billing_period.total_tokens,
                }
            return Response(
                {
                    'error': 'derive-legal infeasible',
                    'violations': result.violations,
                    'message': result.message,
                    'metadata': result.metadata,
                    'billing': billing_payload,
                },
                status=status.HTTP_409_CONFLICT,
            )

        # 3) 寫入新 A 版本 (atomic)
        label = body.get('label') or f'{b_version.version_label} (legal)'
        shift_by_id = {st['id']: st for st in shifts}
        # 用 ShiftTemplate ORM 物件算 duration_hours（含 break_minutes）
        shift_orm = {
            st.id: st for st in ShiftTemplate.objects.filter(id__in=shift_ids)
        }

        with transaction.atomic():
            a_version = ScheduleVersion.objects.create(
                organization=b_version.organization,
                branch=b_version.branch,
                version_label=label,
                version_type='legal',
                period_start=b_version.period_start,
                period_end=b_version.period_end,
                status='draft',
                created_by=request.user,
                derived_from=b_version,
            )
            new_rows = []
            for a in result.assignments:
                d = a['date']
                shift = shift_orm[a['shift_id']]
                new_rows.append(Schedule(
                    schedule_version=a_version,
                    employee_id=a['employee_id'],
                    shift_template_id=a['shift_id'],
                    schedule_date=d if not isinstance(d, str) else _date.fromisoformat(d),
                    expected_hours=round(shift.duration_hours, 2),
                    status='draft',
                ))
            Schedule.objects.bulk_create(new_rows)

        # 4) 計算 diff 摘要 (cells changed / added / removed)
        b_set = {(s.employee_id, s.schedule_date.isoformat(), s.shift_template_id)
                 for s in b_schedules}
        a_set = {(a['employee_id'], a['date'], a['shift_id'])
                 for a in result.assignments}
        removed = sorted(b_set - a_set)
        added = sorted(a_set - b_set)

        billing_payload = None
        if consume_token:
            usage = record_usage(
                organization=b_version.organization,
                billing_mode='derive_legal',
                solver_status='success',
                user=request.user if request.user.is_authenticated else None,
                schedule_version=a_version,
                request_metadata={
                    'derived_from_id': b_version.id,
                    'cells_in_b': len(b_set),
                    'cells_changed': len(removed) + len(added),
                },
            )
            billing_payload = {
                'tokens_charged': usage.tokens_charged,
                'period_usage_after': usage.billing_period.total_tokens,
                'billing_mode': 'derive_legal',
            }

        return Response(
            {
                'legal_version_id': a_version.id,
                'legal_version_label': a_version.version_label,
                'derived_from_id': b_version.id,
                'solver_metadata': result.metadata,
                'diff_summary': {
                    'cells_in_b': len(b_set),
                    'cells_in_a': len(a_set),
                    'cells_unchanged': len(b_set & a_set),
                    'cells_removed_from_b': len(removed),
                    'cells_added_in_a': len(added),
                },
                'removed_cells': [
                    {'employee_id': e, 'date': d, 'shift_id': sid}
                    for (e, d, sid) in removed
                ],
                'added_cells': [
                    {'employee_id': e, 'date': d, 'shift_id': sid}
                    for (e, d, sid) in added
                ],
                'billing': billing_payload,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], url_path='check-compliance')
    def check_compliance(self, request, pk=None):
        """
        一鍵勞基法合規檢查（不寫入 ComplianceCheck 紀錄，純讀取）。

        回傳結構為前端 grid 設計：`violations` 是逐格列表，每筆已對應到
        一個特定的 (employee, schedule_date, shift_template_id) 三元組，可直接
        在 grid 上標記紅色；`summary_by_rule` 給側邊 panel 用。
        客製規則可透過 request body `rules` 覆蓋預設值。
        """
        from apps.compliance.engine import (
            check_schedule_violations,
            summarize_by_rule,
            DEFAULT_RULES,
        )
        from apps.compliance.models import OrgComplianceSettings

        version = self.get_object()
        rules = request.data.get('rules') or None

        # Load the org's soft-rule config so violations get the right
        # severity label (PR11). Caller may override via body `soft_rule_types`.
        soft_rule_types = request.data.get('soft_rule_types')
        if soft_rule_types is None:
            cfg = OrgComplianceSettings.objects.filter(
                organization=version.organization
            ).first()
            soft_rule_types = cfg.soft_rule_types if cfg else []

        try:
            violations = check_schedule_violations(
                version, rules, soft_rule_types=soft_rule_types,
            )
        except (ValueError, TypeError) as exc:
            return Response(
                {'error': f'invalid rules payload: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            'schedule_version_id': version.id,
            'rules_applied': rules or DEFAULT_RULES,
            'soft_rule_types': soft_rule_types,
            'violations': [v.to_dict() for v in violations],
            'summary_by_rule': summarize_by_rule(violations),
            'total_count': len(violations),
        })

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """簽核排班版本"""
        from django.utils import timezone

        version = self.get_object()

        # 使用原子性 update，只在 status=draft 時才更新，避免並發重複簽核
        updated = ScheduleVersion.objects.filter(
            pk=version.pk,
            status='draft'
        ).update(
            status='approved',
            approved_by=request.user,
            approved_at=timezone.now()
        )

        if not updated:
            return Response(
                {'error': 'Only draft versions can be approved, or it was already approved.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        version.refresh_from_db()
        serializer = self.get_serializer(version)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def create_dual_versions(self, request, pk=None):
        """建立雙軌版本（法規版和實際版）"""
        legal_version = self.get_object()
        
        # 建立實際版
        actual_version = ScheduleVersion.objects.create(
            organization=legal_version.organization,
            branch=legal_version.branch,
            version_label=f"{legal_version.version_label} (實際版)",
            version_type='actual',
            period_start=legal_version.period_start,
            period_end=legal_version.period_end,
            status='draft',
            created_by=request.user,
        )
        
        # 複製排班到實際版
        for schedule in legal_version.schedules.all():
            Schedule.objects.create(
                schedule_version=actual_version,
                employee=schedule.employee,
                shift_template=schedule.shift_template,
                schedule_date=schedule.schedule_date,
                expected_hours=schedule.expected_hours,
                status=schedule.status,
                notes=schedule.notes,
            )
        
        serializer = ScheduleVersionSerializer(actual_version)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get'])
    def compare(self, request, pk=None):
        """比對兩個版本的差異"""
        version1 = self.get_object()
        version2_id = request.query_params.get('version2_id')
        
        if not version2_id:
            return Response(
                {'error': 'version2_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            version2 = ScheduleVersion.objects.get(id=version2_id)
        except ScheduleVersion.DoesNotExist:
            return Response(
                {'error': 'Version 2 not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # 比對差異
        schedules1 = {f"{s.employee_id}_{s.schedule_date}_{s.shift_template_id}": s for s in version1.schedules.all()}
        schedules2 = {f"{s.employee_id}_{s.schedule_date}_{s.shift_template_id}": s for s in version2.schedules.all()}
        
        only_in_v1 = [str(k) for k in schedules1.keys() if k not in schedules2]
        only_in_v2 = [str(k) for k in schedules2.keys() if k not in schedules1]
        differences = []
        
        for key in schedules1.keys() & schedules2.keys():
            s1 = schedules1[key]
            s2 = schedules2[key]
            # key 已包含 employee_id / schedule_date / shift_template_id，
            # 故只需比對可能變動的欄位：expected_hours、status、notes
            if (s1.expected_hours != s2.expected_hours
                    or s1.status != s2.status
                    or s1.notes != s2.notes):
                differences.append({
                    'key': key,
                    'version1': ScheduleSerializer(s1).data,
                    'version2': ScheduleSerializer(s2).data,
                })
        
        return Response({
            'version1': ScheduleVersionSerializer(version1).data,
            'version2': ScheduleVersionSerializer(version2).data,
            'only_in_version1': only_in_v1,
            'only_in_version2': only_in_v2,
            'differences': differences,
        })


class ScheduleViewSet(viewsets.ModelViewSet):
    """排班管理"""
    queryset = Schedule.objects.select_related('employee', 'shift_template', 'schedule_version')
    serializer_class = ScheduleSerializer
    permission_classes = [IsSupervisor]
    search_fields = ['employee__employee_id', 'employee__user__username']
    ordering_fields = ['schedule_date', 'created_at']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by schedule_version
        version_id = self.request.query_params.get('version')
        if version_id:
            queryset = queryset.filter(schedule_version_id=version_id)
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee')
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(schedule_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(schedule_date__lte=date_to)
        
        return queryset


class ScheduleChangeViewSet(viewsets.ModelViewSet):
    """排班異動管理"""
    queryset = ScheduleChange.objects.select_related('schedule', 'original_employee', 'replacement_employee', 'changed_by', 'approved_by')
    serializer_class = ScheduleChangeSerializer
    permission_classes = [IsSupervisor]
    search_fields = ['schedule__employee__employee_id', 'reason']
    ordering_fields = ['-changed_at']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by schedule
        schedule_id = self.request.query_params.get('schedule')
        if schedule_id:
            queryset = queryset.filter(schedule_id=schedule_id)
        
        # Filter by change_type
        change_type = self.request.query_params.get('change_type')
        if change_type:
            queryset = queryset.filter(change_type=change_type)
        
        return queryset
