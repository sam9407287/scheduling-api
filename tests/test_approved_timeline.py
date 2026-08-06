"""
Approved-timeline (簽核總表) + cell-acknowledgment tests.

Product rules (2026-08-06):
- Aggregate ALL approved versions; a cell (employee × date) whose content
  differs across covering versions is a discrepancy the manager confirms.
- Overlapping shift times are NEVER filtered or rejected (volunteer-roster
  scenario: tasks legitimately overlap working time).
- Acknowledgments are hash-bound: content change → new hash → re-prompt.
"""
from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status

from apps.accounts.models import Role
from apps.employees.models import Employee
from apps.organizations.models import Organization
from apps.schedules.models import Schedule, ScheduleCellAcknowledgment, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

DAY = date(2026, 8, 3)
TIMELINE_URL = (
    '/api/schedules/approved-timeline/'
    '?version_type=actual&date_from=2026-08-01&date_to=2026-08-31'
)


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
def evening(organization):
    return ShiftTemplate.objects.create(
        organization=organization, name='晚班',
        start_time='16:00', end_time='23:59', min_staff_count=1,
    )


@pytest.fixture
def overlapping(organization):
    """時間與早班重疊的活動班（志工場景）。"""
    return ShiftTemplate.objects.create(
        organization=organization, name='活動班',
        start_time='10:00', end_time='14:00', min_staff_count=1,
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


class TestApprovedTimeline:
    def test_requires_params(self, admin_api_client):
        response = admin_api_client.get('/api/schedules/approved-timeline/?organization=1')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_discrepant_cell_detected(self, admin_api_client, organization, admin_user,
                                      employee, morning, evening):
        v1 = _approved_version(organization, admin_user, 'A')
        v2 = _approved_version(organization, admin_user, 'B')
        _schedule(v1, employee, morning)
        _schedule(v2, employee, evening)

        response = admin_api_client.get(TIMELINE_URL)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['versions']) == 2

        cells = response.data['cells']
        assert len(cells) == 1
        cell = cells[0]
        assert cell['employee_id'] == employee.pk
        assert cell['date'] == DAY.isoformat()
        assert cell['is_discrepant'] is True
        assert cell['acknowledged'] is False
        assert {e['version_id'] for e in cell['entries']} == {v1.pk, v2.pk}

    def test_hash_is_stable(self, admin_api_client, organization, admin_user,
                            employee, morning, evening):
        v1 = _approved_version(organization, admin_user, 'A')
        v2 = _approved_version(organization, admin_user, 'B')
        _schedule(v1, employee, morning)
        _schedule(v2, employee, evening)

        h1 = admin_api_client.get(TIMELINE_URL).data['cells'][0]['content_hash']
        h2 = admin_api_client.get(TIMELINE_URL).data['cells'][0]['content_hash']
        assert h1 == h2

    def test_identical_content_not_discrepant(self, admin_api_client, organization,
                                              admin_user, employee, morning):
        v1 = _approved_version(organization, admin_user, 'A')
        v2 = _approved_version(organization, admin_user, 'B')
        _schedule(v1, employee, morning)
        _schedule(v2, employee, morning)

        response = admin_api_client.get(TIMELINE_URL)
        assert response.data['cells'] == []
        # 兩個版本的班次照樣完整回傳
        assert len(response.data['schedules']) == 2

    def test_overlapping_times_never_filtered(self, admin_api_client, organization,
                                              admin_user, employee, morning, overlapping):
        """時間重疊（早班 08-16 vs 活動班 10-14）不是錯誤，entries 全數保留。"""
        v1 = _approved_version(organization, admin_user, 'A')
        v2 = _approved_version(organization, admin_user, 'B')
        _schedule(v1, employee, morning)
        _schedule(v2, employee, overlapping)

        response = admin_api_client.get(TIMELINE_URL)
        assert response.status_code == status.HTTP_200_OK
        cell = response.data['cells'][0]
        assert len(cell['entries']) == 2
        assert len(response.data['schedules']) == 2

    def test_draft_versions_excluded(self, admin_api_client, organization, admin_user,
                                     employee, morning, evening):
        v1 = _approved_version(organization, admin_user, 'A')
        draft = ScheduleVersion.objects.create(
            organization=organization, version_label='draft', version_type='actual',
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
            created_by=admin_user,
        )
        _schedule(v1, employee, morning)
        _schedule(draft, employee, evening)

        response = admin_api_client.get(TIMELINE_URL)
        assert len(response.data['versions']) == 1
        assert response.data['cells'] == []  # 只有一個 approved 版本覆蓋，無差異可言

    def test_version_type_tracks_are_separate(self, admin_api_client, organization,
                                              admin_user, employee, morning, evening):
        v_actual = _approved_version(organization, admin_user, 'actual-v')
        v_legal = ScheduleVersion.objects.create(
            organization=organization, version_label='legal-v', version_type='legal',
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
            created_by=admin_user,
        )
        ScheduleVersion.objects.filter(pk=v_legal.pk).update(status='approved')
        _schedule(v_actual, employee, morning)
        _schedule(v_legal, employee, evening)

        response = admin_api_client.get(TIMELINE_URL)
        assert len(response.data['versions']) == 1
        assert response.data['cells'] == []

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


class TestCellAcknowledgment:
    def _make_discrepancy(self, admin_api_client, organization, admin_user,
                          employee, shift_a, shift_b):
        v1 = _approved_version(organization, admin_user, 'A')
        v2 = _approved_version(organization, admin_user, 'B')
        _schedule(v1, employee, shift_a)
        _schedule(v2, employee, shift_b)
        cell = admin_api_client.get(TIMELINE_URL).data['cells'][0]
        return v1, v2, cell

    def _ack_payload(self, employee, cell):
        return {
            'employee': employee.pk,
            'schedule_date': cell['date'],
            'version_type': 'actual',
            'content_hash': cell['content_hash'],
        }

    def test_acknowledge_then_timeline_marks_cell(self, admin_api_client, organization,
                                                  admin_user, employee, morning, evening):
        _, _, cell = self._make_discrepancy(
            admin_api_client, organization, admin_user, employee, morning, evening)

        response = admin_api_client.post(
            '/api/schedules/cell-acknowledgments/',
            self._ack_payload(employee, cell), format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['involved']  # 後端自產快照

        cell_after = admin_api_client.get(TIMELINE_URL).data['cells'][0]
        assert cell_after['acknowledged'] is True
        assert cell_after['acknowledged_by']['id'] == admin_user.pk

    def test_repeat_acknowledge_is_idempotent(self, admin_api_client, organization,
                                              admin_user, employee, morning, evening):
        _, _, cell = self._make_discrepancy(
            admin_api_client, organization, admin_user, employee, morning, evening)
        payload = self._ack_payload(employee, cell)

        first = admin_api_client.post('/api/schedules/cell-acknowledgments/', payload, format='json')
        second = admin_api_client.post('/api/schedules/cell-acknowledgments/', payload, format='json')
        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_200_OK
        assert ScheduleCellAcknowledgment.objects.count() == 1

    def test_stale_hash_rejected(self, admin_api_client, organization, admin_user,
                                 employee, morning, evening):
        payload_stale = None
        _, _, cell = self._make_discrepancy(
            admin_api_client, organization, admin_user, employee, morning, evening)
        payload_stale = self._ack_payload(employee, cell)
        payload_stale['content_hash'] = 'f' * 64

        response = admin_api_client.post(
            '/api/schedules/cell-acknowledgments/', payload_stale, format='json',
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data['code'] == 'discrepancy_changed'

    def test_content_change_invalidates_acknowledgment(self, admin_api_client, organization,
                                                       admin_user, employee, morning, evening,
                                                       overlapping):
        v1, v2, cell = self._make_discrepancy(
            admin_api_client, organization, admin_user, employee, morning, evening)
        admin_api_client.post(
            '/api/schedules/cell-acknowledgments/',
            self._ack_payload(employee, cell), format='json',
        )

        # 取消簽核 → 改內容 → 重新簽核：hash 改變，認可失效
        admin_api_client.post(f'/api/schedules/versions/{v2.pk}/unapprove/',
                              {'reason': 'edit'}, format='json')
        Schedule.objects.filter(schedule_version=v2).delete()
        _schedule(v2, employee, overlapping)
        admin_api_client.post(f'/api/schedules/versions/{v2.pk}/approve/')

        cell_after = admin_api_client.get(TIMELINE_URL).data['cells'][0]
        assert cell_after['content_hash'] != cell['content_hash']
        assert cell_after['acknowledged'] is False
