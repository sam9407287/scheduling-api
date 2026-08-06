"""
Approved-timeline (簽核總表) conflict groups + overlap decisions.

Contract (FEATURE_HANDOFF_APPROVAL_SUMMARY.md, 2026-08-06):
- GET /api/schedules/versions/approved-timeline/ returns versions,
  schedules (incl. previous-day cross-midnight rows), conflicts (cross-
  version time-intersection groups per employee) and
  unresolved_conflict_count.
- Same-version combinations are never conflicts; versions from different
  branches still conflict. Overlaps never block saving or approving.
- POST /api/schedules/overlap-decisions/ resolves a group: select keeps a
  non-overlapping subset; coexist keeps all and requires a comment. Stale
  conflict_key (member edited) → 409 conflict_changed.
- Version periods are display metadata: created as today, auto-expanded by
  schedule writes, read-only via the API.
"""
from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status

from apps.employees.models import Employee
from apps.organizations.models import Organization
from apps.schedules.models import Schedule, ScheduleOverlapDecision, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

DAY = date(2026, 8, 3)
TIMELINE_URL = (
    '/api/schedules/versions/approved-timeline/'
    '?version_type=actual&date_from=2026-08-01&date_to=2026-08-31'
)
DECISION_URL = '/api/schedules/overlap-decisions/'


@pytest.fixture
def employee(employee_user, organization, branch):
    return Employee.objects.create(
        user=employee_user, employee_id='EMP1',
        organization=organization, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


@pytest.fixture
def morning(organization):
    return ShiftTemplate.objects.create(
        organization=organization, name='早班',
        start_time='08:00', end_time='16:00', min_staff_count=1,
    )


@pytest.fixture
def overlapping(organization):
    """時間與早班相交的活動班。"""
    return ShiftTemplate.objects.create(
        organization=organization, name='活動班',
        start_time='10:00', end_time='14:00', min_staff_count=1,
    )


@pytest.fixture
def evening(organization):
    return ShiftTemplate.objects.create(
        organization=organization, name='晚班',
        start_time='16:00', end_time='23:00', min_staff_count=1,
    )


@pytest.fixture
def night(organization):
    """跨午夜班。"""
    return ShiftTemplate.objects.create(
        organization=organization, name='夜班',
        start_time='22:00', end_time='06:00', min_staff_count=1,
    )


def _approved_version(organization, user, label):
    version = ScheduleVersion.objects.create(
        organization=organization,
        version_label=label, version_type='actual',
        period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
        created_by=user,
    )
    ScheduleVersion.objects.filter(pk=version.pk).update(status='approved')
    version.refresh_from_db()
    return version


def _schedule(version, employee, shift, day=DAY):
    return Schedule.objects.create(
        schedule_version=version, employee=employee,
        shift_template=shift, schedule_date=day,
        expected_hours=Decimal('8.00'), status='assigned',
    )


def _make_conflict(admin_api_client, organization, admin_user, employee,
                   shift_a, shift_b):
    v1 = _approved_version(organization, admin_user, 'A')
    v2 = _approved_version(organization, admin_user, 'B')
    s1 = _schedule(v1, employee, shift_a)
    s2 = _schedule(v2, employee, shift_b)
    conflict = admin_api_client.get(TIMELINE_URL).data['conflicts'][0]
    return v1, v2, s1, s2, conflict


class TestApprovedTimeline:
    def test_requires_params(self, admin_api_client):
        response = admin_api_client.get('/api/schedules/versions/approved-timeline/')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cross_version_time_overlap_is_conflict(self, admin_api_client, organization,
                                                    admin_user, employee, morning, overlapping):
        v1, v2, s1, s2, conflict = _make_conflict(
            admin_api_client, organization, admin_user, employee, morning, overlapping)

        response = admin_api_client.get(TIMELINE_URL)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['versions']) == 2
        assert len(response.data['schedules']) == 2
        assert response.data['unresolved_conflict_count'] == 1

        assert conflict['employee_id'] == employee.pk
        assert conflict['schedule_ids'] == sorted([s1.pk, s2.pk])
        assert len(conflict['schedules']) == 2
        assert conflict['decision'] is None
        assert conflict['conflict_key']

    def test_non_overlapping_times_not_conflict(self, admin_api_client, organization,
                                                admin_user, employee, morning, evening):
        v1 = _approved_version(organization, admin_user, 'A')
        v2 = _approved_version(organization, admin_user, 'B')
        _schedule(v1, employee, morning)   # 08-16
        _schedule(v2, employee, evening)   # 16-23

        response = admin_api_client.get(TIMELINE_URL)
        assert response.data['conflicts'] == []
        assert response.data['unresolved_conflict_count'] == 0
        assert len(response.data['schedules']) == 2

    def test_same_version_combine_not_conflict(self, admin_api_client, organization,
                                               admin_user, employee, morning, overlapping):
        v1 = _approved_version(organization, admin_user, 'A')
        _schedule(v1, employee, morning)
        _schedule(v1, employee, overlapping)

        response = admin_api_client.get(TIMELINE_URL)
        assert response.data['conflicts'] == []

    def test_draft_versions_excluded(self, admin_api_client, organization, admin_user,
                                     employee, morning, overlapping):
        v1 = _approved_version(organization, admin_user, 'A')
        draft = ScheduleVersion.objects.create(
            organization=organization, version_label='draft', version_type='actual',
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
            created_by=admin_user,
        )
        _schedule(v1, employee, morning)
        _schedule(draft, employee, overlapping)

        response = admin_api_client.get(TIMELINE_URL)
        assert response.data['conflicts'] == []
        assert len(response.data['schedules']) == 1

    def test_previous_day_cross_midnight_included(self, admin_api_client, organization,
                                                  admin_user, employee, night, morning):
        """7/31 夜班 22-06 跨進 8/1：查 8 月要看得到並與 8/1 早班衝突。

        （夜班 06:00 結束、早班 08:00 開始其實不相交——改用 8/1 早班和
        7/31 夜班「跨到 8/1 06:00」確認 schedules 有納入即可。）
        """
        v1 = _approved_version(organization, admin_user, 'A')
        _schedule(v1, employee, night, day=date(2026, 7, 31))

        response = admin_api_client.get(TIMELINE_URL)
        assert len(response.data['schedules']) == 1
        assert response.data['schedules'][0]['schedule_date'] == '2026-07-31'

    def test_previous_day_normal_shift_excluded(self, admin_api_client, organization,
                                                admin_user, employee, morning):
        v1 = _approved_version(organization, admin_user, 'A')
        _schedule(v1, employee, morning, day=date(2026, 7, 31))

        response = admin_api_client.get(TIMELINE_URL)
        assert response.data['schedules'] == []

    def test_branch_filters_by_employee_branch(self, admin_api_client, organization,
                                               admin_user, employee, morning, branch):
        from apps.organizations.models import Branch
        other_branch = Branch.objects.create(
            organization=organization, name='南店', code='BR02',
            address='x', phone='02-2',
        )
        v1 = _approved_version(organization, admin_user, 'A')
        _schedule(v1, employee, morning)

        matched = admin_api_client.get(f'{TIMELINE_URL}&branch={branch.pk}')
        assert len(matched.data['schedules']) == 1
        unmatched = admin_api_client.get(f'{TIMELINE_URL}&branch={other_branch.pk}')
        assert unmatched.data['schedules'] == []

    def test_org_isolation(self, supervisor_api_client, admin_user, employee, morning):
        other_org = Organization.objects.create(
            name='他機構', code='OTHER', address='x', phone='02-1', email='o@x.com',
        )
        other_version = ScheduleVersion.objects.create(
            organization=other_org, version_label='X', version_type='actual',
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
        )
        ScheduleVersion.objects.filter(pk=other_version.pk).update(status='approved')

        response = supervisor_api_client.get(TIMELINE_URL)
        assert response.status_code == status.HTTP_200_OK
        assert all(v['organization'] != other_org.pk for v in response.data['versions'])


class TestOverlapDecision:
    def test_select_decision(self, admin_api_client, organization, admin_user,
                             employee, morning, overlapping):
        _, _, s1, s2, conflict = _make_conflict(
            admin_api_client, organization, admin_user, employee, morning, overlapping)

        response = admin_api_client.post(DECISION_URL, {
            'conflict_key': conflict['conflict_key'],
            'schedule_ids': conflict['schedule_ids'],
            'decision': 'select',
            'selected_schedule_ids': [s1.pk],
            'comment': '',
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['decision'] == 'select'
        assert response.data['selected_schedule_ids'] == [s1.pk]
        assert response.data['decided_by_name'] == 'admin'

        after = admin_api_client.get(TIMELINE_URL).data
        assert after['unresolved_conflict_count'] == 0
        assert after['conflicts'][0]['decision']['decision'] == 'select'

    def test_select_rejects_overlapping_selection(self, admin_api_client, organization,
                                                  admin_user, employee, morning, overlapping):
        _, _, s1, s2, conflict = _make_conflict(
            admin_api_client, organization, admin_user, employee, morning, overlapping)

        response = admin_api_client.post(DECISION_URL, {
            'conflict_key': conflict['conflict_key'],
            'schedule_ids': conflict['schedule_ids'],
            'decision': 'select',
            'selected_schedule_ids': [s1.pk, s2.pk],  # 兩筆彼此重疊
            'comment': '',
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_coexist_requires_comment(self, admin_api_client, organization, admin_user,
                                      employee, morning, overlapping):
        _, _, _, _, conflict = _make_conflict(
            admin_api_client, organization, admin_user, employee, morning, overlapping)

        missing = admin_api_client.post(DECISION_URL, {
            'conflict_key': conflict['conflict_key'],
            'schedule_ids': conflict['schedule_ids'],
            'decision': 'coexist',
            'selected_schedule_ids': conflict['schedule_ids'],
            'comment': '  ',
        }, format='json')
        assert missing.status_code == status.HTTP_400_BAD_REQUEST

        ok = admin_api_client.post(DECISION_URL, {
            'conflict_key': conflict['conflict_key'],
            'schedule_ids': conflict['schedule_ids'],
            'decision': 'coexist',
            'selected_schedule_ids': conflict['schedule_ids'],
            'comment': '支援性重疊，主管已確認',
        }, format='json')
        assert ok.status_code == status.HTTP_201_CREATED
        assert ok.data['selected_schedule_ids'] == conflict['schedule_ids']

    def test_stale_conflict_key_rejected(self, admin_api_client, organization, admin_user,
                                         employee, morning, overlapping):
        _, v2, _, s2, conflict = _make_conflict(
            admin_api_client, organization, admin_user, employee, morning, overlapping)

        # 改動群組成員 → updated_at 變 → 舊 key 過期
        Schedule.objects.filter(pk=s2.pk).update(notes='touched')
        s2.refresh_from_db()
        s2.save()  # bump updated_at

        response = admin_api_client.post(DECISION_URL, {
            'conflict_key': conflict['conflict_key'],
            'schedule_ids': conflict['schedule_ids'],
            'decision': 'coexist',
            'selected_schedule_ids': conflict['schedule_ids'],
            'comment': 'x',
        }, format='json')
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data['code'] == 'conflict_changed'

    def test_same_key_updates_existing_decision(self, admin_api_client, organization,
                                                admin_user, employee, morning, overlapping):
        _, _, s1, s2, conflict = _make_conflict(
            admin_api_client, organization, admin_user, employee, morning, overlapping)
        payload = {
            'conflict_key': conflict['conflict_key'],
            'schedule_ids': conflict['schedule_ids'],
            'decision': 'select',
            'selected_schedule_ids': [s1.pk],
            'comment': '',
        }
        first = admin_api_client.post(DECISION_URL, payload, format='json')
        second = admin_api_client.post(DECISION_URL, {
            **payload,
            'decision': 'coexist',
            'selected_schedule_ids': conflict['schedule_ids'],
            'comment': '改判並存',
        }, format='json')
        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_200_OK
        assert ScheduleOverlapDecision.objects.count() == 1
        assert ScheduleOverlapDecision.objects.get().decision == 'coexist'


class TestVersionPeriodAutomation:
    """period 是後端自動維護的資料涵蓋範圍，不再由使用者輸入。"""

    def test_create_version_without_dates(self, admin_api_client, organization):
        response = admin_api_client.post('/api/schedules/versions/', {
            'organization': organization.pk,
            'version_label': '無期間版本',
            'version_type': 'actual',
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['period_start'] == response.data['period_end']

    def test_period_input_is_ignored(self, admin_api_client, organization):
        from django.utils import timezone
        response = admin_api_client.post('/api/schedules/versions/', {
            'organization': organization.pk,
            'version_label': 'x',
            'version_type': 'actual',
            'period_start': '2020-01-01',
            'period_end': '2020-12-31',
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['period_start'] == timezone.localdate().isoformat()

    def test_schedule_write_expands_period(self, admin_api_client, organization,
                                           admin_user, employee, morning):
        version_id = admin_api_client.post('/api/schedules/versions/', {
            'organization': organization.pk,
            'version_label': '擴張測試',
            'version_type': 'actual',
        }, format='json').data['id']

        admin_api_client.post('/api/schedules/schedules/', {
            'schedule_version': version_id, 'employee': employee.pk,
            'shift_template': morning.pk, 'schedule_date': '2026-12-25',
            'expected_hours': '8.00', 'status': 'assigned',
        }, format='json')
        admin_api_client.post('/api/schedules/schedules/', {
            'schedule_version': version_id, 'employee': employee.pk,
            'shift_template': morning.pk, 'schedule_date': '2026-01-05',
            'expected_hours': '8.00', 'status': 'assigned',
        }, format='json')

        version = ScheduleVersion.objects.get(pk=version_id)
        assert version.period_start == date(2026, 1, 5)
        assert version.period_end == date(2026, 12, 25)

    def test_period_never_shrinks_on_delete(self, admin_api_client, organization,
                                            admin_user, employee, morning):
        version_id = admin_api_client.post('/api/schedules/versions/', {
            'organization': organization.pk,
            'version_label': '不縮測試',
            'version_type': 'actual',
        }, format='json').data['id']
        row = admin_api_client.post('/api/schedules/schedules/', {
            'schedule_version': version_id, 'employee': employee.pk,
            'shift_template': morning.pk, 'schedule_date': '2026-12-25',
            'expected_hours': '8.00', 'status': 'assigned',
        }, format='json').data['id']
        admin_api_client.delete(f'/api/schedules/schedules/{row}/')

        version = ScheduleVersion.objects.get(pk=version_id)
        assert version.period_end == date(2026, 12, 25)
