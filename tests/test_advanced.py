"""
Advanced tests - login, clock in/out, schedule version compare,
compliance checks, permission edge cases, CRUD operations
"""
import pytest
from datetime import date, time, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.organizations.models import Organization, Branch
from apps.employees.models import Employee, Certification, Contract
from apps.shifts.models import ShiftTemplate, ShiftRule
from apps.schedules.models import ScheduleVersion, Schedule, ScheduleChange
from apps.attendance.models import Attendance, AnomalyRecord
from apps.overtime.models import OvertimeRecord, OvertimeRule
from apps.compliance.models import LaborLawRule, ComplianceCheck

User = get_user_model()


# =============================================================================
# Login API Tests
# =============================================================================

class TestLoginAPI:
    def test_login_success(self, api_client, admin_user):
        """登入成功取得 token"""
        response = api_client.post('/api/auth/login/', {
            'username': 'admin',
            'password': 'testpass123',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert 'token' in response.data
        assert 'user' in response.data
        assert response.data['user']['username'] == 'admin'

    def test_login_wrong_password(self, api_client, admin_user):
        """錯誤密碼應拒絕"""
        response = api_client.post('/api/auth/login/', {
            'username': 'admin',
            'password': 'wrongpassword',
        }, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_missing_fields(self, api_client):
        """缺少欄位應拒絕"""
        response = api_client.post('/api/auth/login/', {
            'username': 'admin',
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_nonexistent_user(self, api_client, db):
        """不存在的使用者應拒絕"""
        response = api_client.post('/api/auth/login/', {
            'username': 'nonexistent',
            'password': 'testpass123',
        }, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_token_reuse(self, api_client, admin_user):
        """重複登入應返回相同 token"""
        response1 = api_client.post('/api/auth/login/', {
            'username': 'admin',
            'password': 'testpass123',
        }, format='json')
        response2 = api_client.post('/api/auth/login/', {
            'username': 'admin',
            'password': 'testpass123',
        }, format='json')
        assert response1.data['token'] == response2.data['token']

    def test_authenticated_request_with_token(self, api_client, admin_user):
        """用 token 認證後可以訪問 API"""
        # Login first
        login_response = api_client.post('/api/auth/login/', {
            'username': 'admin',
            'password': 'testpass123',
        }, format='json')
        token = login_response.data['token']

        # Use token to access protected endpoint
        api_client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        response = api_client.get('/api/auth/users/me/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['username'] == 'admin'


# =============================================================================
# User Profile Tests
# =============================================================================

class TestUserProfileAPI:
    def test_get_profile(self, admin_api_client, admin_user):
        """取得使用者資料"""
        response = admin_api_client.get('/api/auth/users/me/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['username'] == admin_user.username

    def test_update_profile(self, admin_api_client):
        """更新使用者資料"""
        response = admin_api_client.patch('/api/auth/users/update_profile/', {
            'phone': '0912345678',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['phone'] == '0912345678'


# =============================================================================
# Clock In / Clock Out Tests
# =============================================================================

class TestClockInOutAPI:
    @pytest.fixture
    def employee_with_profile(self, db, employee_user, organization, branch):
        return Employee.objects.create(
            user=employee_user,
            employee_id='EMP_CLOCK',
            organization=organization,
            branch=branch,
            position='護理師',
            contract_type='full_time',
            hire_date=date(2024, 1, 1),
        )

    def test_clock_in(self, employee_api_client, employee_with_profile):
        """上班打卡"""
        response = employee_api_client.post('/api/attendance/attendances/clock_in/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['clock_in'] is not None

    def test_clock_in_duplicate(self, employee_api_client, employee_with_profile):
        """重複打卡應拒絕"""
        employee_api_client.post('/api/attendance/attendances/clock_in/')
        response = employee_api_client.post('/api/attendance/attendances/clock_in/')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'Already clocked in' in response.data['error']

    def test_clock_out(self, employee_api_client, employee_with_profile):
        """下班打卡"""
        employee_api_client.post('/api/attendance/attendances/clock_in/')
        response = employee_api_client.post('/api/attendance/attendances/clock_out/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['clock_out'] is not None

    def test_clock_out_without_clock_in(self, employee_api_client, employee_with_profile):
        """未打上班卡就打下班卡"""
        response = employee_api_client.post('/api/attendance/attendances/clock_out/')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_clock_without_employee_profile(self, admin_api_client):
        """無員工檔案的使用者打卡應報錯"""
        response = admin_api_client.post('/api/attendance/attendances/clock_in/')
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Schedule Version Compare Tests
# =============================================================================

class TestScheduleVersionCompare:
    @pytest.fixture
    def two_versions(self, db, organization, branch, admin_user, employee_user):
        employee = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_CMP',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        shift = ShiftTemplate.objects.create(
            organization=organization,
            name='早班',
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=60,
            min_staff_count=1,
        )
        shift2 = ShiftTemplate.objects.create(
            organization=organization,
            name='夜班',
            start_time=time(22, 0),
            end_time=time(6, 0),
            break_minutes=60,
            min_staff_count=1,
        )
        v1 = ScheduleVersion.objects.create(
            organization=organization,
            branch=branch,
            version_label='法規版',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        v2 = ScheduleVersion.objects.create(
            organization=organization,
            branch=branch,
            version_label='實際版',
            version_type='actual',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        # Same schedule in both versions
        Schedule.objects.create(
            schedule_version=v1,
            employee=employee,
            shift_template=shift,
            schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('7.00'),
        )
        Schedule.objects.create(
            schedule_version=v2,
            employee=employee,
            shift_template=shift,
            schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('7.00'),
        )
        # Extra schedule only in v2
        Schedule.objects.create(
            schedule_version=v2,
            employee=employee,
            shift_template=shift2,
            schedule_date=date(2024, 3, 2),
            expected_hours=Decimal('7.00'),
        )
        return v1, v2, employee

    def test_compare_versions(self, admin_api_client, two_versions):
        """比較兩個排班版本的差異"""
        v1, v2, _ = two_versions
        response = admin_api_client.get(
            f'/api/schedules/versions/{v1.pk}/compare/?version2_id={v2.pk}'
        )
        assert response.status_code == status.HTTP_200_OK
        assert 'only_in_version1' in response.data
        assert 'only_in_version2' in response.data
        assert len(response.data['only_in_version2']) >= 1

    def test_compare_missing_version2(self, admin_api_client, two_versions):
        """比較時缺少 version2_id"""
        v1, _, _ = two_versions
        response = admin_api_client.get(f'/api/schedules/versions/{v1.pk}/compare/')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_compare_nonexistent_version2(self, admin_api_client, two_versions):
        """比較不存在的 version2"""
        v1, _, _ = two_versions
        response = admin_api_client.get(
            f'/api/schedules/versions/{v1.pk}/compare/?version2_id=9999'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Dual Track Version Tests
# =============================================================================

class TestDualTrackSchedule:
    @pytest.fixture
    def legal_version_with_schedules(self, db, organization, branch, admin_user, employee_user):
        employee = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_DUAL',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        shift = ShiftTemplate.objects.create(
            organization=organization,
            name='早班',
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=60,
        )
        version = ScheduleVersion.objects.create(
            organization=organization,
            branch=branch,
            version_label='2024-03 法規版',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        Schedule.objects.create(
            schedule_version=version,
            employee=employee,
            shift_template=shift,
            schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('7.00'),
        )
        return version

    def test_create_dual_version_copies_schedules(self, admin_api_client, legal_version_with_schedules):
        """建立雙軌版本時應複製排班"""
        version = legal_version_with_schedules
        response = admin_api_client.post(
            f'/api/schedules/versions/{version.pk}/create_dual_versions/'
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['version_type'] == 'actual'

        # Check that schedules were copied
        actual_version_id = response.data['id']
        actual_version = ScheduleVersion.objects.get(id=actual_version_id)
        assert actual_version.schedules.count() == 1
        assert actual_version.version_label.endswith('(實際版)')


# =============================================================================
# Schedule Change Tests
# =============================================================================

class TestScheduleChangeAPI:
    @pytest.fixture
    def schedule_with_employees(self, db, organization, branch, admin_user, employee_user):
        emp1 = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_CHG1',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        # Create second user for replacement
        user2 = User.objects.create_user(
            username='employee2',
            email='emp2@example.com',
            password='testpass123',
        )
        emp2 = Employee.objects.create(
            user=user2,
            employee_id='EMP_CHG2',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        shift = ShiftTemplate.objects.create(
            organization=organization,
            name='早班',
            start_time=time(8, 0),
            end_time=time(16, 0),
        )
        version = ScheduleVersion.objects.create(
            organization=organization,
            version_label='Test',
            version_type='actual',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        schedule = Schedule.objects.create(
            schedule_version=version,
            employee=emp1,
            shift_template=shift,
            schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('8.00'),
        )
        return schedule, emp1, emp2, admin_user

    def test_create_schedule_change(self, admin_api_client, schedule_with_employees):
        """建立排班異動（代班）"""
        schedule, emp1, emp2, admin_user = schedule_with_employees
        data = {
            'schedule': schedule.pk,
            'change_type': 'substitute',
            'original_employee': emp1.pk,
            'replacement_employee': emp2.pk,
            'reason': '員工請假',
            'changed_by': admin_user.pk,
        }
        response = admin_api_client.post('/api/schedules/changes/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['change_type'] == 'substitute'

    def test_list_schedule_changes(self, admin_api_client, schedule_with_employees):
        """列出排班異動"""
        response = admin_api_client.get('/api/schedules/changes/')
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# Compliance Check Tests
# =============================================================================

class TestComplianceCheckAPI:
    def test_check_attendance_compliance(self, admin_api_client, organization):
        """出勤合規檢查"""
        data = {
            'organization_id': organization.pk,
            'period_start': '2024-03-01',
            'period_end': '2024-03-31',
        }
        response = admin_api_client.post(
            '/api/compliance/checks/check_attendance/', data, format='json'
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['check_type'] == 'attendance'

    def test_check_attendance_missing_fields(self, admin_api_client):
        """出勤合規檢查缺少欄位"""
        data = {'organization_id': 1}
        response = admin_api_client.post(
            '/api/compliance/checks/check_attendance/', data, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# Employee CRUD Tests
# =============================================================================

class TestEmployeeCRUD:
    def test_create_employee(self, admin_api_client, organization, branch):
        """建立員工"""
        # Create user first
        user = User.objects.create_user(
            username='new_emp',
            email='new_emp@example.com',
            password='testpass123',
        )
        data = {
            'user_id': user.pk,
            'employee_id': 'EMP_NEW',
            'organization': organization.pk,
            'branch': branch.pk,
            'position': '照服員',
            'contract_type': 'full_time',
            'agreed_hours_per_week': '40.00',
            'hire_date': '2024-06-01',
        }
        response = admin_api_client.post('/api/employees/employees/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['employee_id'] == 'EMP_NEW'

    def test_add_certification_to_employee(self, admin_api_client, organization, branch, employee_user):
        """為員工新增證照"""
        emp = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_CERT',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        cert = Certification.objects.create(
            name='護理師執照',
            code='NURSE_TEST',
        )
        response = admin_api_client.post(
            f'/api/employees/employees/{emp.pk}/add_certification/',
            {'certification_id': cert.pk},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert emp.certifications.count() == 1

    def test_remove_certification_from_employee(self, admin_api_client, organization, branch, employee_user):
        """移除員工證照"""
        emp = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_CERT2',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        cert = Certification.objects.create(
            name='照服員證照',
            code='CARE_TEST',
        )
        emp.certifications.add(cert)
        assert emp.certifications.count() == 1

        response = admin_api_client.delete(
            f'/api/employees/employees/{emp.pk}/remove_certification/',
            {'certification_id': cert.pk},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert emp.certifications.count() == 0

    def test_add_contract_to_employee(self, admin_api_client, organization, branch, employee_user):
        """為員工新增合約"""
        emp = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_CONTRACT',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        data = {
            'employee': emp.pk,
            'contract_type': 'full_time',
            'start_date': '2024-06-01',
            'base_salary': '35000.00',
            'agreed_hours_per_week': '40.00',
        }
        response = admin_api_client.post(
            f'/api/employees/employees/{emp.pk}/add_contract/',
            data,
            format='json'
        )
        assert response.status_code == status.HTTP_201_CREATED


# =============================================================================
# Overtime Calculation Tests
# =============================================================================

class TestOvertimeCalculation:
    def test_calculate_overtime_from_attendance(self, admin_api_client, organization, branch, employee_user, admin_user):
        """從出勤記錄計算加班"""
        emp = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_OT',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        shift = ShiftTemplate.objects.create(
            organization=organization,
            name='早班',
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=60,
        )
        version = ScheduleVersion.objects.create(
            organization=organization,
            version_label='OT Test',
            version_type='actual',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        Schedule.objects.create(
            schedule_version=version,
            employee=emp,
            shift_template=shift,
            schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('7.00'),
        )
        attendance = Attendance.objects.create(
            employee=emp,
            work_date=date(2024, 3, 1),
            clock_in=timezone.now().replace(hour=8, minute=0),
            clock_out=timezone.now().replace(hour=19, minute=0),
            actual_hours=Decimal('11.00'),
        )
        response = admin_api_client.post('/api/overtime/records/calculate/', {
            'attendance_id': attendance.pk,
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_calculate_overtime_missing_attendance(self, admin_api_client):
        """加班計算缺少 attendance_id"""
        response = admin_api_client.post('/api/overtime/records/calculate/', {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# Permission Tests
# =============================================================================

class TestPermissions:
    def test_employee_cannot_create_organization(self, employee_api_client):
        """員工不能建立機構"""
        data = {'name': '新機構', 'code': 'NEW'}
        response = employee_api_client.post('/api/organizations/organizations/', data, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_cannot_manage_users(self, employee_api_client):
        """員工不能管理使用者"""
        response = employee_api_client.get('/api/auth/users/')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_can_view_attendance(self, employee_api_client):
        """員工可以查看自己的出勤"""
        response = employee_api_client.get('/api/attendance/attendances/')
        assert response.status_code == status.HTTP_200_OK

    def test_supervisor_can_manage_schedules(self, supervisor_api_client, organization, admin_user):
        """主管可以管理排班"""
        ScheduleVersion.objects.create(
            organization=organization,
            version_label='Supervisor Test',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        response = supervisor_api_client.get('/api/schedules/versions/')
        assert response.status_code == status.HTTP_200_OK

    def test_employee_cannot_manage_schedules(self, employee_api_client):
        """員工不能管理排班"""
        response = employee_api_client.get('/api/schedules/versions/')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_cannot_access_compliance(self, employee_api_client):
        """員工不能管理勞基法規則"""
        response = employee_api_client.post('/api/compliance/rules/', {
            'name': 'test',
            'rule_type': 'max_weekly_hours',
            'value': '40',
        }, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_manager_can_manage_employees(self, manager_api_client):
        """管理者可以管理員工"""
        response = manager_api_client.get('/api/employees/employees/')
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# Anomaly Resolution Tests
# =============================================================================

class TestAnomalyResolution:
    @pytest.fixture
    def anomaly_record(self, db, organization, branch, employee_user):
        emp = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_ANO',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        attendance = Attendance.objects.create(
            employee=emp,
            work_date=date(2024, 3, 1),
            actual_hours=Decimal('13.00'),
            anomaly_flag=True,
            anomaly_reason='超時工作',
        )
        return AnomalyRecord.objects.create(
            attendance=attendance,
            anomaly_type='overtime',
            description='工作時數超過12小時',
            severity='high',
        )

    def test_resolve_anomaly(self, admin_api_client, anomaly_record):
        """處理異常紀錄"""
        response = admin_api_client.post(
            f'/api/attendance/anomalies/{anomaly_record.pk}/resolve/',
            {'notes': '已確認為正常加班'},
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['resolved'] is True

    def test_list_unresolved_anomalies(self, admin_api_client, anomaly_record):
        """列出未處理的異常"""
        response = admin_api_client.get('/api/attendance/anomalies/?resolved=false')
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) >= 1

    def test_filter_anomalies_by_severity(self, admin_api_client, anomaly_record):
        """依嚴重程度過濾異常"""
        response = admin_api_client.get('/api/attendance/anomalies/?severity=high')
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) >= 1


# =============================================================================
# Shift Template Duration Tests
# =============================================================================

class TestShiftTemplateDuration:
    def test_normal_shift_duration(self, db, organization):
        """正常班別時數"""
        shift = ShiftTemplate.objects.create(
            organization=organization,
            name='日班',
            start_time=time(9, 0),
            end_time=time(18, 0),
            break_minutes=60,
        )
        assert shift.duration_hours == 8.0  # 9h - 1h break

    def test_no_break_shift(self, db, organization):
        """無休息班別"""
        shift = ShiftTemplate.objects.create(
            organization=organization,
            name='短班',
            start_time=time(14, 0),
            end_time=time(18, 0),
            break_minutes=0,
        )
        assert shift.duration_hours == 4.0

    def test_cross_midnight_shift(self, db, organization):
        """跨午夜班別"""
        shift = ShiftTemplate.objects.create(
            organization=organization,
            name='大夜',
            start_time=time(23, 0),
            end_time=time(7, 0),
            break_minutes=30,
        )
        assert shift.duration_hours == 7.5  # 8h - 0.5h break


# =============================================================================
# Compliance Engine Tests
# =============================================================================

class TestComplianceEngine:
    def test_check_schedule_compliance_pass(self, admin_api_client, organization, admin_user):
        """合規檢查通過 - 無排班"""
        version = ScheduleVersion.objects.create(
            organization=organization,
            version_label='Empty',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        response = admin_api_client.post(
            '/api/compliance/checks/check_schedule/',
            {'schedule_version_id': version.pk},
            format='json'
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['status'] == 'pass'

    def test_check_schedule_compliance_nonexistent(self, admin_api_client):
        """合規檢查 - 不存在的排班版本"""
        response = admin_api_client.post(
            '/api/compliance/checks/check_schedule/',
            {'schedule_version_id': 9999},
            format='json'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Attendance Calculate Hours Tests
# =============================================================================

class TestAttendanceModel:
    def test_calculate_hours(self, db, organization, branch, employee_user):
        """計算工作時數"""
        emp = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_CALC',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        now = timezone.now()
        attendance = Attendance.objects.create(
            employee=emp,
            work_date=now.date(),
            clock_in=now.replace(hour=8, minute=0, second=0),
            clock_out=now.replace(hour=17, minute=0, second=0),
        )
        hours = attendance.calculate_hours()
        assert hours == Decimal('9.00')

    def test_calculate_hours_no_clock_out(self, db, organization, branch, employee_user):
        """只有上班打卡，無法計算"""
        emp = Employee.objects.create(
            user=employee_user,
            employee_id='EMP_CALC2',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        attendance = Attendance.objects.create(
            employee=emp,
            work_date=timezone.now().date(),
            clock_in=timezone.now(),
        )
        hours = attendance.calculate_hours()
        assert hours is None
