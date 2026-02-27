"""
Test API endpoints for all apps
"""
import pytest
from datetime import date, time
from decimal import Decimal
from django.contrib.auth import get_user_model
from rest_framework import status

from apps.organizations.models import Organization, Branch
from apps.employees.models import Employee, Certification, Contract
from apps.shifts.models import ShiftTemplate, ShiftRule
from apps.schedules.models import ScheduleVersion, Schedule
from apps.attendance.models import Attendance
from apps.overtime.models import OvertimeRule
from apps.compliance.models import LaborLawRule

User = get_user_model()


# =============================================================================
# Auth API Tests
# =============================================================================

class TestAuthAPI:
    def test_unauthenticated_access_denied(self, api_client):
        """未認證的請求應被拒絕"""
        response = api_client.get('/api/auth/users/')
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    def test_get_current_user_profile(self, admin_api_client, admin_user):
        """取得當前使用者資料"""
        response = admin_api_client.get('/api/auth/users/me/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['username'] == admin_user.username
        assert response.data['email'] == admin_user.email

    def test_list_users_as_admin(self, admin_api_client):
        """管理員可以列出使用者"""
        response = admin_api_client.get('/api/auth/users/')
        assert response.status_code == status.HTTP_200_OK

    def test_list_users_as_employee_denied(self, employee_api_client):
        """一般員工無法列出使用者"""
        response = employee_api_client.get('/api/auth/users/')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_roles(self, admin_api_client, admin_role):
        """列出角色"""
        response = admin_api_client.get('/api/auth/roles/')
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) >= 1


# =============================================================================
# Organizations API Tests
# =============================================================================

class TestOrganizationAPI:
    def test_list_organizations(self, manager_api_client, organization):
        """列出機構"""
        response = manager_api_client.get('/api/organizations/organizations/')
        assert response.status_code == status.HTTP_200_OK

    def test_create_organization(self, admin_api_client):
        """建立機構"""
        data = {
            'name': '新機構',
            'code': 'NEW',
            'address': '新北市',
            'phone': '02-99999999',
            'email': 'new@example.com',
        }
        response = admin_api_client.post('/api/organizations/organizations/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['name'] == '新機構'
        assert response.data['code'] == 'NEW'

    def test_get_organization_detail(self, admin_api_client, organization):
        """取得機構詳情"""
        response = admin_api_client.get(f'/api/organizations/organizations/{organization.pk}/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['name'] == organization.name

    def test_update_organization(self, admin_api_client, organization):
        """更新機構"""
        data = {'name': '更新後的機構名稱'}
        response = admin_api_client.patch(
            f'/api/organizations/organizations/{organization.pk}/',
            data,
            format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['name'] == '更新後的機構名稱'

    def test_delete_organization(self, admin_api_client, organization):
        """刪除機構"""
        response = admin_api_client.delete(f'/api/organizations/organizations/{organization.pk}/')
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert Organization.objects.count() == 0


class TestBranchAPI:
    def test_list_branches(self, admin_api_client, branch):
        """列出分店"""
        response = admin_api_client.get('/api/organizations/branches/')
        assert response.status_code == status.HTTP_200_OK

    def test_create_branch(self, admin_api_client, organization):
        """建立分店"""
        data = {
            'organization': organization.pk,
            'name': '新分店',
            'code': 'BR02',
            'address': '新地址',
        }
        response = admin_api_client.post('/api/organizations/branches/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['name'] == '新分店'

    def test_filter_branches_by_organization(self, admin_api_client, branch, organization):
        """依機構過濾分店"""
        response = admin_api_client.get(f'/api/organizations/branches/?organization={organization.pk}')
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# Employees API Tests
# =============================================================================

class TestEmployeeAPI:
    @pytest.fixture
    def employee(self, db, employee_user, organization, branch):
        return Employee.objects.create(
            user=employee_user,
            employee_id='EMP001',
            organization=organization,
            branch=branch,
            position='護理師',
            contract_type='full_time',
            agreed_hours_per_week=Decimal('40.00'),
            hire_date=date(2024, 1, 1),
        )

    def test_list_employees(self, admin_api_client, employee):
        """列出員工"""
        response = admin_api_client.get('/api/employees/employees/')
        assert response.status_code == status.HTTP_200_OK

    def test_get_employee_detail(self, admin_api_client, employee):
        """取得員工詳情"""
        response = admin_api_client.get(f'/api/employees/employees/{employee.pk}/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['employee_id'] == 'EMP001'

    def test_get_employee_contracts(self, admin_api_client, employee):
        """取得員工合約"""
        Contract.objects.create(
            employee=employee,
            contract_type='full_time',
            start_date=date(2024, 1, 1),
            base_salary=Decimal('35000.00'),
        )
        response = admin_api_client.get(f'/api/employees/employees/{employee.pk}/contracts/')
        assert response.status_code == status.HTTP_200_OK

    def test_filter_employees_by_active(self, admin_api_client, employee):
        """依在職狀態過濾"""
        response = admin_api_client.get('/api/employees/employees/?is_active=true')
        assert response.status_code == status.HTTP_200_OK


class TestCertificationAPI:
    def test_create_certification(self, admin_api_client):
        """建立證照"""
        data = {
            'name': '護理師執照',
            'code': 'NURSE',
            'description': '護理師資格',
            'is_required': True,
        }
        response = admin_api_client.post('/api/employees/certifications/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_list_certifications(self, admin_api_client):
        """列出證照"""
        Certification.objects.create(name='護理師', code='NURSE')
        response = admin_api_client.get('/api/employees/certifications/')
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# Shifts API Tests
# =============================================================================

class TestShiftTemplateAPI:
    def test_create_shift_template(self, admin_api_client, organization):
        """建立班別模板"""
        data = {
            'organization': organization.pk,
            'name': '早班',
            'start_time': '08:00:00',
            'end_time': '16:00:00',
            'break_minutes': 60,
            'min_staff_count': 2,
        }
        response = admin_api_client.post('/api/shifts/templates/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['name'] == '早班'

    def test_list_shift_templates(self, admin_api_client, organization):
        """列出班別模板"""
        ShiftTemplate.objects.create(
            organization=organization,
            name='早班',
            start_time=time(8, 0),
            end_time=time(16, 0),
        )
        response = admin_api_client.get('/api/shifts/templates/')
        assert response.status_code == status.HTTP_200_OK


class TestShiftRuleAPI:
    def test_create_shift_rule(self, admin_api_client, organization):
        """建立排班規則"""
        data = {
            'organization': organization.pk,
            'name': '最大連續天數',
            'rule_type': 'max_consecutive_days',
            'value': {'max_days': 6},
        }
        response = admin_api_client.post('/api/shifts/rules/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_list_shift_rules(self, admin_api_client, organization):
        """列出排班規則"""
        response = admin_api_client.get('/api/shifts/rules/')
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# Schedule API Tests
# =============================================================================

class TestScheduleVersionAPI:
    def test_create_schedule_version(self, admin_api_client, organization, branch, admin_user):
        """建立排班版本"""
        data = {
            'organization': organization.pk,
            'branch': branch.pk,
            'version_label': '2024年3月法規版',
            'version_type': 'legal',
            'period_start': '2024-03-01',
            'period_end': '2024-03-31',
        }
        response = admin_api_client.post('/api/schedules/versions/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['version_type'] == 'legal'

    def test_list_schedule_versions(self, admin_api_client, organization, admin_user):
        """列出排班版本"""
        ScheduleVersion.objects.create(
            organization=organization,
            version_label='Test',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        response = admin_api_client.get('/api/schedules/versions/')
        assert response.status_code == status.HTTP_200_OK

    def test_approve_schedule_version(self, admin_api_client, organization, admin_user):
        """簽核排班版本"""
        version = ScheduleVersion.objects.create(
            organization=organization,
            version_label='Test',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        response = admin_api_client.post(f'/api/schedules/versions/{version.pk}/approve/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'approved'

    def test_create_dual_versions(self, admin_api_client, organization, admin_user, employee_user, branch):
        """建立雙軌版本"""
        version = ScheduleVersion.objects.create(
            organization=organization,
            branch=branch,
            version_label='法規版',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        response = admin_api_client.post(f'/api/schedules/versions/{version.pk}/create_dual_versions/')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['version_type'] == 'actual'

    def test_filter_by_version_type(self, admin_api_client, organization, admin_user):
        """依版本類型過濾"""
        ScheduleVersion.objects.create(
            organization=organization,
            version_label='法規版',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        response = admin_api_client.get('/api/schedules/versions/?version_type=legal')
        assert response.status_code == status.HTTP_200_OK


class TestScheduleAPI:
    def test_list_schedules(self, admin_api_client):
        """列出排班"""
        response = admin_api_client.get('/api/schedules/schedules/')
        assert response.status_code == status.HTTP_200_OK

    def test_filter_by_date_range(self, admin_api_client):
        """依日期範圍過濾"""
        response = admin_api_client.get(
            '/api/schedules/schedules/?date_from=2024-03-01&date_to=2024-03-31'
        )
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# Attendance API Tests
# =============================================================================

class TestAttendanceAPI:
    def test_list_attendance(self, admin_api_client):
        """列出出勤紀錄"""
        response = admin_api_client.get('/api/attendance/attendances/')
        assert response.status_code == status.HTTP_200_OK

    def test_filter_by_date(self, admin_api_client):
        """依日期過濾出勤"""
        response = admin_api_client.get(
            '/api/attendance/attendances/?date_from=2024-03-01&date_to=2024-03-31'
        )
        assert response.status_code == status.HTTP_200_OK

    def test_filter_by_anomaly(self, admin_api_client):
        """過濾異常出勤"""
        response = admin_api_client.get('/api/attendance/attendances/?anomaly=true')
        assert response.status_code == status.HTTP_200_OK


class TestAnomalyRecordAPI:
    def test_list_anomalies(self, admin_api_client):
        """列出異常紀錄"""
        response = admin_api_client.get('/api/attendance/anomalies/')
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# Overtime API Tests
# =============================================================================

class TestOvertimeAPI:
    def test_create_overtime_rule(self, admin_api_client, organization):
        """建立加班規則"""
        data = {
            'organization': organization.pk,
            'overtime_type': 'regular',
            'multiplier': '1.34',
            'max_hours_per_day': '4.00',
            'max_hours_per_month': '46.00',
        }
        response = admin_api_client.post('/api/overtime/rules/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_list_overtime_records(self, admin_api_client):
        """列出加班紀錄"""
        response = admin_api_client.get('/api/overtime/records/')
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# Compliance API Tests
# =============================================================================

class TestComplianceAPI:
    def test_create_labor_law_rule(self, admin_api_client):
        """建立勞基法規則"""
        data = {
            'name': '每週最大工時',
            'rule_type': 'max_weekly_hours',
            'value': '40.00',
            'description': '每週不超過40小時',
        }
        response = admin_api_client.post('/api/compliance/rules/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_list_compliance_checks(self, admin_api_client):
        """列出合規檢查"""
        response = admin_api_client.get('/api/compliance/checks/')
        assert response.status_code == status.HTTP_200_OK

    def test_check_schedule_compliance(self, admin_api_client, organization, admin_user):
        """排班合規檢查"""
        version = ScheduleVersion.objects.create(
            organization=organization,
            version_label='Test',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        data = {'schedule_version_id': version.pk}
        response = admin_api_client.post('/api/compliance/checks/check_schedule/', data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['status'] == 'pass'


# =============================================================================
# AI Engine API Tests
# =============================================================================

class TestAIEngineAPI:
    def test_generate_schedule_no_data(self, admin_api_client, organization, branch):
        """AI 排班 - 缺少資料"""
        data = {}
        response = admin_api_client.post('/api/ai/schedule/generate/', data, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_generate_schedule_validation(self, admin_api_client, organization, branch):
        """AI 排班 - 驗證必填欄位"""
        data = {
            'organization_id': organization.pk,
            'period_start': '2024-03-01',
            'period_end': '2024-03-31',
        }
        response = admin_api_client.post('/api/ai/schedule/generate/', data, format='json')
        # Should not crash even with no employees/shifts
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_employee_cannot_access_ai(self, employee_api_client, organization):
        """員工不能使用 AI 排班"""
        data = {
            'organization_id': organization.pk,
            'period_start': '2024-03-01',
            'period_end': '2024-03-31',
        }
        response = employee_api_client.post('/api/ai/schedule/generate/', data, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# Swagger/OpenAPI Tests
# =============================================================================

class TestSwaggerAPI:
    def test_schema_endpoint(self, admin_api_client):
        """OpenAPI Schema 端點"""
        response = admin_api_client.get('/api/schema/')
        assert response.status_code == status.HTTP_200_OK

    def test_swagger_ui(self, api_client):
        """Swagger UI 可訪問"""
        response = api_client.get('/api/docs/')
        assert response.status_code == status.HTTP_200_OK

    def test_redoc(self, api_client):
        """ReDoc 可訪問"""
        response = api_client.get('/api/redoc/')
        assert response.status_code == status.HTTP_200_OK
