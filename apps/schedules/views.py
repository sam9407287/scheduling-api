"""
Schedule views
"""
from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django.utils.dateparse import parse_date
from .models import Schedule, ScheduleVersion, ScheduleChange, ScheduleOverlapDecision
from .serializers import (
    ScheduleSerializer,
    ScheduleVersionSerializer,
    ScheduleChangeSerializer,
    ScheduleOverlapDecisionSerializer
)
from . import overlaps as overlaps_module
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

        # Org isolation: non-superusers only ever see their own organization
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(organization=self.request.user.organization)
            else:
                queryset = queryset.none()

        # Optional explicit organization filter (superuser cross-org view)
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
        # 優先序：預設 8 < org ShiftRule < request constraints
        from apps.shifts.rules import resolve_max_daily_hours
        org_daily_cap = resolve_max_daily_hours(b_version.organization_id)
        if org_daily_cap is not None:
            labor_law_defaults['max_daily_hours'] = org_daily_cap
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
    def unapprove(self, request, pk=None):
        """取消簽核：approved → draft，版本恢復可編輯。

        不做期間重疊檢查——多個已簽核版本並存屬正常狀態。
        """
        from django.contrib.contenttypes.models import ContentType
        from apps.audit.models import AuditLog

        version = self.get_object()

        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response(
                {'error': 'reason is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        old_approved_by_id = version.approved_by_id
        old_approved_at = version.approved_at

        # 原子轉換：只在 approved 時才回 draft，避免並發重複取消
        updated = ScheduleVersion.objects.filter(
            pk=version.pk,
            status='approved'
        ).update(
            status='draft',
            approved_by=None,
            approved_at=None
        )

        if not updated:
            return Response(
                {
                    'code': 'unapprove_conflict',
                    'error': 'Only approved versions can be unapproved.',
                },
                status=status.HTTP_409_CONFLICT
            )

        # filter().update() 不觸發 post_save，稽核需手動落地（含取消原因）
        AuditLog.objects.create(
            user=request.user,
            action='cancel',
            model_name='ScheduleVersion',
            record_id=version.pk,
            content_type=ContentType.objects.get_for_model(ScheduleVersion),
            object_id=version.pk,
            old_data={
                'status': 'approved',
                'approved_by': old_approved_by_id,
                'approved_at': old_approved_at.isoformat() if old_approved_at else None,
            },
            new_data={'status': 'draft', 'approved_by': None, 'approved_at': None},
            changes={'reason': reason},
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

    def _resolve_org_id(self, request):
        """Org isolation：非 superuser 一律鎖定自己的機構。

        Superuser 可用 ?organization= 跨機構查詢，未指定時退回自己的機構。
        """
        if request.user.is_superuser:
            org_id = request.query_params.get('organization')
            if org_id:
                return int(org_id)
        return request.user.organization_id

    # 註冊於 /api/schedules/versions/approved-timeline/（見 urls.py，非 router action）
    def approved_timeline(self, request):
        """簽核總表：approved 版本、範圍內班次、跨版本時間重疊群組與既有裁決。

        重疊只是資訊，不阻擋任何操作；同版本內的 combine 不算衝突，
        版本分店不同仍算（同一人不能同時在兩處上班）。
        """
        org_id = self._resolve_org_id(request)
        if not org_id:
            return Response({'error': 'organization is required'}, status=status.HTTP_400_BAD_REQUEST)

        version_type = request.query_params.get('version_type')
        if version_type not in ('legal', 'actual'):
            return Response(
                {'error': 'version_type must be "legal" or "actual"'},
                status=status.HTTP_400_BAD_REQUEST
            )

        date_from = parse_date(request.query_params.get('date_from') or '')
        date_to = parse_date(request.query_params.get('date_to') or '')
        if not date_from or not date_to or date_from > date_to:
            return Response(
                {'error': 'valid date_from and date_to are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if (date_to - date_from).days > 62:
            return Response(
                {'error': 'date range must not exceed 62 days'},
                status=status.HTTP_400_BAD_REQUEST
            )

        branch_param = request.query_params.get('branch')
        branch_id = int(branch_param) if branch_param and branch_param != 'all' else None

        schedules = overlaps_module.timeline_schedules(
            org_id, version_type, date_from, date_to, branch_id=branch_id,
        )
        conflicts = overlaps_module.annotate_decisions(
            overlaps_module.build_conflicts(schedules), org_id,
        )

        version_ids = {s.schedule_version_id for s in schedules}
        versions = ScheduleVersion.objects.filter(
            organization_id=org_id,
            version_type=version_type,
            status='approved',
        ).filter(
            Q(period_start__lte=date_to, period_end__gte=date_from)
            | Q(pk__in=version_ids)
        ).distinct().select_related('organization', 'branch', 'approved_by', 'created_by')

        conflict_payload = [
            {
                'conflict_key': c['conflict_key'],
                'starts_at': c['starts_at'],
                'ends_at': c['ends_at'],
                'employee_id': c['employee_id'],
                'schedule_ids': c['schedule_ids'],
                'schedules': ScheduleSerializer(c['schedules'], many=True).data,
                'decision': (
                    ScheduleOverlapDecisionSerializer(c['decision']).data
                    if c['decision'] else None
                ),
            }
            for c in conflicts
        ]
        return Response({
            'versions': ScheduleVersionSerializer(versions, many=True).data,
            'schedules': ScheduleSerializer(schedules, many=True).data,
            'conflicts': conflict_payload,
            'unresolved_conflict_count': sum(
                1 for c in conflicts if c['decision'] is None
            ),
        })

    # 註冊於 /api/schedules/day-overview/（見 urls.py，非 router action）
    def day_overview(self, request):
        """某日在其他版本已存在的班次總覽（純資訊，不做衝突判斷）。"""
        org_id = self._resolve_org_id(request)
        if not org_id:
            return Response({'error': 'organization is required'}, status=status.HTTP_400_BAD_REQUEST)

        date = parse_date(request.query_params.get('date') or '')
        if not date:
            return Response({'error': 'valid date is required'}, status=status.HTTP_400_BAD_REQUEST)

        schedules = Schedule.objects.filter(
            schedule_version__organization_id=org_id,
            schedule_date=date,
        ).select_related('employee', 'shift_template', 'schedule_version', 'schedule_version__branch')

        if request.query_params.get('include_archived', 'false').lower() != 'true':
            schedules = schedules.exclude(schedule_version__status='archived')

        exclude_version = request.query_params.get('exclude_version')
        if exclude_version:
            schedules = schedules.exclude(schedule_version_id=exclude_version)

        employee_id = request.query_params.get('employee')
        if employee_id:
            schedules = schedules.filter(employee_id=employee_id)

        by_version = {}
        for s in schedules:
            by_version.setdefault(s.schedule_version, []).append(s)

        entries = [
            {
                'version': {
                    'id': version.pk,
                    'version_label': version.version_label,
                    'version_type': version.version_type,
                    'status': version.status,
                    'branch': version.branch_id,
                    'branch_name': version.branch.name if version.branch else None,
                    'period_start': version.period_start.isoformat(),
                    'period_end': version.period_end.isoformat(),
                },
                'schedules': ScheduleSerializer(rows, many=True).data,
            }
            for version, rows in sorted(by_version.items(), key=lambda kv: kv[0].pk)
        ]
        return Response({'date': date.isoformat(), 'entries': entries})


class ScheduleOverlapDecisionViewSet(mixins.CreateModelMixin,
                                     mixins.ListModelMixin,
                                     viewsets.GenericViewSet):
    """簽核總表重疊裁決：select（保留不重疊子集）/ coexist（全數並存＋備註）。"""
    queryset = ScheduleOverlapDecision.objects.select_related('employee', 'decided_by')
    serializer_class = ScheduleOverlapDecisionSerializer
    permission_classes = [IsSupervisor]

    def get_queryset(self):
        queryset = super().get_queryset()
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(organization=self.request.user.organization)
            else:
                queryset = queryset.none()

        employee_id = self.request.query_params.get('employee')
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        date_from = self.request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(schedule_date__gte=date_from)
        date_to = self.request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(schedule_date__lte=date_to)
        return queryset

    def create(self, request, *args, **kwargs):
        conflict_key = request.data.get('conflict_key') or ''
        schedule_ids = request.data.get('schedule_ids') or []
        decision = request.data.get('decision')
        selected_ids = request.data.get('selected_schedule_ids') or []
        comment = (request.data.get('comment') or '').strip()

        if decision not in ('select', 'coexist'):
            return Response(
                {'error': 'decision must be "select" or "coexist"'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not conflict_key or not schedule_ids:
            return Response(
                {'error': 'conflict_key and schedule_ids are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        org = request.user.organization
        if request.user.is_superuser and not org:
            first = Schedule.objects.filter(pk__in=schedule_ids).select_related(
                'schedule_version').first()
            org = first.schedule_version.organization if first else None
        if not org:
            return Response({'error': 'organization is required'}, status=status.HTTP_400_BAD_REQUEST)

        # 重算 live 群組：缺少候選、群組已變、key 過期都拒絕
        version_type = None
        probe = Schedule.objects.filter(pk__in=schedule_ids).select_related(
            'schedule_version').first()
        if probe:
            version_type = probe.schedule_version.version_type
        group, current_key = overlaps_module.find_current_group(
            org.pk, version_type, schedule_ids,
        ) if version_type else (None, None)
        if group is None or current_key != conflict_key:
            return Response(
                {
                    'code': 'conflict_changed',
                    'error': 'Conflict group changed; refresh the summary.',
                },
                status=status.HTTP_409_CONFLICT
            )

        group_ids = {s.pk for s in group}
        if decision == 'select':
            selected = set(selected_ids)
            if not selected or not selected <= group_ids:
                return Response(
                    {'error': 'selected_schedule_ids must be a non-empty subset of the group'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            # 被保留的班次彼此不得重疊
            kept = [s for s in group if s.pk in selected]
            for i, a in enumerate(kept):
                for b in kept[i + 1:]:
                    a_start, a_end = overlaps_module._interval(a)
                    b_start, b_end = overlaps_module._interval(b)
                    if a.schedule_version_id != b.schedule_version_id \
                            and a_start < b_end and b_start < a_end:
                        return Response(
                            {'error': 'selected schedules must not overlap each other'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
        else:  # coexist
            if not comment:
                return Response(
                    {'error': 'comment is required for coexist decisions'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            selected = group_ids

        employee = group[0].employee
        obj, _created = ScheduleOverlapDecision.objects.update_or_create(
            organization=org,
            conflict_key=conflict_key,
            defaults={
                'branch': employee.branch,
                'version_type': version_type,
                'employee': employee,
                'schedule_date': min(s.schedule_date for s in group),
                'schedule_ids': sorted(group_ids),
                'decision': decision,
                'selected_schedule_ids': sorted(selected),
                'comment': comment,
                'decided_by': request.user,
            },
        )
        return Response(
            self.get_serializer(obj).data,
            status=status.HTTP_201_CREATED if _created else status.HTTP_200_OK
        )


class ScheduleViewSet(viewsets.ModelViewSet):
    """排班管理

    已簽核（非 draft）版本的班表唯讀：所有寫入動作回 409
    `schedule_version_locked`，需先 unapprove 才能編輯。
    """
    queryset = Schedule.objects.select_related('employee', 'shift_template', 'schedule_version')
    serializer_class = ScheduleSerializer
    permission_classes = [IsSupervisor]
    search_fields = ['employee__employee_id', 'employee__user__username']
    ordering_fields = ['schedule_date', 'created_at']

    LOCKED_RESPONSE = {
        'code': 'schedule_version_locked',
        'error': 'Approved schedule versions are read-only.',
    }

    def _target_version_locked(self, request):
        """檢查 request body 指向的 schedule_version 是否非 draft（含 org isolation）。"""
        version_id = request.data.get('schedule_version')
        if not version_id:
            return False
        versions = ScheduleVersion.objects.all()
        if not request.user.is_superuser:
            versions = versions.filter(organization=request.user.organization)
        version = versions.filter(pk=version_id).first()
        return version is not None and version.status != 'draft'

    def create(self, request, *args, **kwargs):
        if self._target_version_locked(request):
            return Response(self.LOCKED_RESPONSE, status=status.HTTP_409_CONFLICT)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        # 現有版本或 body 想搬入的新版本任一非 draft 都鎖
        if instance.schedule_version.status != 'draft' or self._target_version_locked(request):
            return Response(self.LOCKED_RESPONSE, status=status.HTTP_409_CONFLICT)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.schedule_version.status != 'draft':
            return Response(self.LOCKED_RESPONSE, status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()

        # Org isolation: non-superusers only see schedules in their own organization
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(schedule_version__organization=self.request.user.organization)
            else:
                queryset = queryset.none()

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

        # Org isolation: non-superusers only see changes in their own organization
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(schedule__schedule_version__organization=self.request.user.organization)
            else:
                queryset = queryset.none()

        # Filter by schedule
        schedule_id = self.request.query_params.get('schedule')
        if schedule_id:
            queryset = queryset.filter(schedule_id=schedule_id)
        
        # Filter by change_type
        change_type = self.request.query_params.get('change_type')
        if change_type:
            queryset = queryset.filter(change_type=change_type)
        
        return queryset
