"""
Test models for all apps
"""
import pytest
from datetime import date, time
from decimal import Decimal
from django.contrib.auth import get_user_model
from apps.accounts.models import Role
from apps.organizations.models import Organization, Branch
from apps.employees.models import Employee, Contract, Certification
from apps.shifts.models import ShiftTemplate, ShiftRule
from apps.schedules.models import Schedule, ScheduleVersion, ScheduleChange
from apps.attendance.models import Attendance, AnomalyRecord
from apps.overtime.models import OvertimeRecord, OvertimeRule
from apps.compliance.models import LaborLawRule, ComplianceCheck
from apps.audit.models import AuditLog

User = get_user_model()


# =============================================================================
# Accounts Models
# =============================================================================

class TestRoleModel:
    def test_create_role(self, db):
        role = Role.objects.create(
            name='admin',
            description='系統管理員',
            permissions={'all': True}
        )
        assert role.pk is not None
        assert str(role) == '系統管理員'

    def test_role_choices(self, admin_role):
        assert admin_role.name == 'admin'
        assert admin_role.get_name_display() == '系統管理員'


class TestUserModel:
    def test_create_user(self, db, admin_role, organization):
        user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
            role=admin_role,
            organization=organization,
        )
        assert user.pk is not None
        assert user.username == 'testuser'
        assert user.check_password('testpass123')
        assert str(user) == 'testuser'

    def test_user_with_firebase_uid(self, db):
        user = User.objects.create_user(
            username='firebase_user',
            email='firebase@example.com',
            password='testpass123',
            firebase_uid='firebase_uid_123',
        )
        assert user.firebase_uid == 'firebase_uid_123'


# =============================================================================
# Organization Models
# =============================================================================

class TestOrganizationModel:
    def test_create_organization(self, organization):
        assert organization.pk is not None
        assert organization.name == '測試機構'
        assert organization.code == 'TEST'
        assert organization.is_active is True
        assert str(organization) == '測試機構'

    def test_organization_unique_code(self, db, organization):
        with pytest.raises(Exception):
            Organization.objects.create(
                name='另一個機構',
                code='TEST',  # same code
            )


class TestBranchModel:
    def test_create_branch(self, branch, organization):
        assert branch.pk is not None
        assert branch.organization == organization
        assert branch.name == '測試分店'
        assert str(branch) == '測試機構 - 測試分店'

    def test_branch_unique_together(self, db, branch, organization):
        with pytest.raises(Exception):
            Branch.objects.create(
                organization=organization,
                name='另一分店',
                code='BR01',  # same code + same org
            )


# =============================================================================
# Employee Models
# =============================================================================

class TestEmployeeModel:
    def test_create_employee(self, db, employee_user, organization, branch):
        employee = Employee.objects.create(
            user=employee_user,
            employee_id='EMP001',
            organization=organization,
            branch=branch,
            position='護理師',
            contract_type='full_time',
            agreed_hours_per_week=Decimal('40.00'),
            hire_date=date(2024, 1, 1),
        )
        assert employee.pk is not None
        assert employee.employee_id == 'EMP001'
        assert employee.contract_type == 'full_time'

    def test_employee_unique_id(self, db, employee_user, admin_user, organization, branch):
        Employee.objects.create(
            user=employee_user,
            employee_id='EMP001',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        with pytest.raises(Exception):
            Employee.objects.create(
                user=admin_user,
                employee_id='EMP001',  # duplicate
                organization=organization,
                branch=branch,
                position='護理師',
                hire_date=date(2024, 1, 1),
            )


class TestCertificationModel:
    def test_create_certification(self, db):
        cert = Certification.objects.create(
            name='護理師執照',
            code='NURSE',
            description='護理師資格證書',
            is_required=True,
        )
        assert cert.pk is not None
        assert cert.name == '護理師執照'
        assert str(cert) == '護理師執照'


class TestContractModel:
    def test_create_contract(self, db, employee_user, organization, branch):
        employee = Employee.objects.create(
            user=employee_user,
            employee_id='EMP001',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        contract = Contract.objects.create(
            employee=employee,
            contract_type='full_time',
            start_date=date(2024, 1, 1),
            base_salary=Decimal('35000.00'),
            agreed_hours_per_week=Decimal('40.00'),
        )
        assert contract.pk is not None
        assert contract.base_salary == Decimal('35000.00')


# =============================================================================
# Shift Models
# =============================================================================

class TestShiftTemplateModel:
    def test_create_shift_template(self, db, organization):
        shift = ShiftTemplate.objects.create(
            organization=organization,
            name='早班',
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=60,
            min_staff_count=2,
        )
        assert shift.pk is not None
        assert shift.name == '早班'
        assert shift.duration_hours == 7.0  # 8h - 1h break

    def test_shift_template_cross_midnight(self, db, organization):
        shift = ShiftTemplate.objects.create(
            organization=organization,
            name='夜班',
            start_time=time(22, 0),
            end_time=time(6, 0),
            break_minutes=60,
            min_staff_count=1,
        )
        assert shift.duration_hours == 7.0  # 8h - 1h break


class TestShiftRuleModel:
    def test_create_shift_rule(self, db, organization):
        rule = ShiftRule.objects.create(
            organization=organization,
            name='最大連續工作天數',
            rule_type='max_consecutive_days',
            value={'max_days': 6},
        )
        assert rule.pk is not None
        assert rule.rule_type == 'max_consecutive_days'


# =============================================================================
# Schedule Models
# =============================================================================

class TestScheduleVersionModel:
    def test_create_schedule_version(self, db, organization, branch, admin_user):
        version = ScheduleVersion.objects.create(
            organization=organization,
            branch=branch,
            version_label='2024年3月法規版',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            status='draft',
            created_by=admin_user,
        )
        assert version.pk is not None
        assert version.version_type == 'legal'
        assert version.status == 'draft'


class TestScheduleModel:
    def test_create_schedule(self, db, organization, branch, admin_user, employee_user):
        version = ScheduleVersion.objects.create(
            organization=organization,
            branch=branch,
            version_label='2024年3月',
            version_type='legal',
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            created_by=admin_user,
        )
        employee = Employee.objects.create(
            user=employee_user,
            employee_id='EMP001',
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
        schedule = Schedule.objects.create(
            schedule_version=version,
            employee=employee,
            shift_template=shift,
            schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('7.00'),
        )
        assert schedule.pk is not None
        assert schedule.status == 'draft'


# =============================================================================
# Attendance Models
# =============================================================================

class TestAttendanceModel:
    def test_create_attendance(self, db, employee_user, organization, branch):
        employee = Employee.objects.create(
            user=employee_user,
            employee_id='EMP001',
            organization=organization,
            branch=branch,
            position='護理師',
            hire_date=date(2024, 1, 1),
        )
        attendance = Attendance.objects.create(
            employee=employee,
            work_date=date(2024, 3, 1),
        )
        assert attendance.pk is not None
        assert attendance.anomaly_flag is False


# =============================================================================
# Overtime Models
# =============================================================================

class TestOvertimeRuleModel:
    def test_create_overtime_rule(self, db, organization):
        rule = OvertimeRule.objects.create(
            organization=organization,
            overtime_type='regular',
            multiplier=Decimal('1.34'),
            max_hours_per_day=Decimal('4.00'),
            max_hours_per_month=Decimal('46.00'),
        )
        assert rule.pk is not None
        assert rule.multiplier == Decimal('1.34')


# =============================================================================
# Compliance Models
# =============================================================================

class TestLaborLawRuleModel:
    def test_create_labor_law_rule(self, db):
        rule = LaborLawRule.objects.create(
            name='每週最大工時',
            rule_type='max_weekly_hours',
            value=Decimal('40.00'),
            description='勞基法規定每週工作時數不得超過40小時',
        )
        assert rule.pk is not None
        assert rule.rule_type == 'max_weekly_hours'


class TestComplianceCheckModel:
    def test_create_compliance_check(self, db, organization, admin_user):
        check = ComplianceCheck.objects.create(
            organization=organization,
            check_type='schedule',
            check_period_start=date(2024, 3, 1),
            check_period_end=date(2024, 3, 31),
            status='pass',
            violations=[],
            warnings=[],
            checked_by=admin_user,
        )
        assert check.pk is not None
        assert check.status == 'pass'


# =============================================================================
# Audit Models
# =============================================================================

class TestAuditLogModel:
    def test_create_audit_log(self, db, admin_user):
        log = AuditLog.objects.create(
            user=admin_user,
            action='create',
            model_name='organizations.organization',
            record_id=1,
            new_data={'name': '測試機構'},
        )
        assert log.pk is not None
        assert log.action == 'create'
