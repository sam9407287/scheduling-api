"""
Management command to seed test data
"""
from datetime import date, time, timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.accounts.models import Role
from apps.organizations.models import Organization, Branch
from apps.employees.models import Employee, Contract, Certification
from apps.shifts.models import ShiftTemplate, ShiftRule
from apps.schedules.models import ScheduleVersion, Schedule
from apps.overtime.models import OvertimeRule
from apps.compliance.models import LaborLawRule

User = get_user_model()


class Command(BaseCommand):
    help = '建立測試用種子資料'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='清除現有資料後再建立',
        )

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('清除現有資料...')
            self._clear_data()

        self.stdout.write('開始建立種子資料...')

        # 1. 建立角色
        roles = self._create_roles()
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立 {len(roles)} 個角色'))

        # 2. 建立機構和分店
        org, branches = self._create_organizations()
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立機構和 {len(branches)} 個分店'))

        # 3. 建立使用者
        users = self._create_users(roles, org, branches)
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立 {len(users)} 個使用者'))

        # 4. 建立證照
        certs = self._create_certifications()
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立 {len(certs)} 個證照'))

        # 5. 建立員工
        employees = self._create_employees(users, org, branches, certs)
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立 {len(employees)} 個員工'))

        # 6. 建立班別模板
        shifts = self._create_shift_templates(org, certs)
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立 {len(shifts)} 個班別模板'))

        # 7. 建立排班規則
        rules = self._create_shift_rules(org)
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立 {len(rules)} 個排班規則'))

        # 8. 建立加班規則
        ot_rules = self._create_overtime_rules(org)
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立 {len(ot_rules)} 個加班規則'))

        # 9. 建立勞基法規則
        law_rules = self._create_labor_law_rules()
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立 {len(law_rules)} 個勞基法規則'))

        # 10. 建立範例排班
        schedule_count = self._create_sample_schedules(org, branches, employees, shifts, users)
        self.stdout.write(self.style.SUCCESS(f'  ✓ 建立 {schedule_count} 筆排班'))

        self.stdout.write(self.style.SUCCESS('\n✅ 種子資料建立完成！'))
        self.stdout.write(self.style.WARNING('\n登入帳號：'))
        self.stdout.write(f'  Admin:      admin / admin123')
        self.stdout.write(f'  Manager:    manager / manager123')
        self.stdout.write(f'  Supervisor: supervisor / super123')
        self.stdout.write(f'  Employees:  emp01~emp10 / emp123')

    def _clear_data(self):
        """清除所有資料"""
        Schedule.objects.all().delete()
        ScheduleVersion.objects.all().delete()
        Contract.objects.all().delete()
        Employee.objects.all().delete()
        Certification.objects.all().delete()
        ShiftTemplate.objects.all().delete()
        ShiftRule.objects.all().delete()
        OvertimeRule.objects.all().delete()
        LaborLawRule.objects.all().delete()
        Branch.objects.all().delete()
        Organization.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()
        Role.objects.all().delete()

    def _create_roles(self):
        roles = {}
        for name, desc in [
            ('admin', '系統管理員'),
            ('manager', '管理者'),
            ('supervisor', '主管'),
            ('employee', '員工'),
        ]:
            role, _ = Role.objects.get_or_create(
                name=name,
                defaults={'description': desc, 'permissions': {}}
            )
            roles[name] = role
        return roles

    def _create_organizations(self):
        org, _ = Organization.objects.get_or_create(
            code='CARE01',
            defaults={
                'name': '幸福長照機構',
                'address': '台北市信義區松仁路100號',
                'phone': '02-12345678',
                'email': 'info@happycare.com',
            }
        )
        branches = []
        for code, name, addr in [
            ('HQ', '總部', '台北市信義區松仁路100號'),
            ('BR01', '信義分店', '台北市信義區忠孝東路1號'),
            ('BR02', '大安分店', '台北市大安區敦化南路2號'),
            ('BR03', '中山分店', '台北市中山區南京東路3號'),
        ]:
            branch, _ = Branch.objects.get_or_create(
                organization=org,
                code=code,
                defaults={
                    'name': name,
                    'address': addr,
                    'phone': '02-11111111',
                }
            )
            branches.append(branch)
        return org, branches

    def _create_users(self, roles, org, branches):
        users = {}

        # Admin
        admin, _ = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@happycare.com',
                'first_name': '系統',
                'last_name': '管理員',
                'is_staff': True,
                'is_superuser': True,
                'role': roles['admin'],
                'organization': org,
            }
        )
        admin.set_password('admin123')
        admin.save()
        users['admin'] = admin

        # Manager
        manager, _ = User.objects.get_or_create(
            username='manager',
            defaults={
                'email': 'manager@happycare.com',
                'first_name': '王',
                'last_name': '經理',
                'is_staff': True,
                'role': roles['manager'],
                'organization': org,
                'branch': branches[0],
            }
        )
        manager.set_password('manager123')
        manager.save()
        users['manager'] = manager

        # Supervisor
        supervisor, _ = User.objects.get_or_create(
            username='supervisor',
            defaults={
                'email': 'supervisor@happycare.com',
                'first_name': '李',
                'last_name': '主管',
                'is_staff': True,
                'role': roles['supervisor'],
                'organization': org,
                'branch': branches[1],
            }
        )
        supervisor.set_password('super123')
        supervisor.save()
        users['supervisor'] = supervisor

        # Employees
        emp_names = [
            ('陳', '小明'), ('林', '小華'), ('張', '小美'),
            ('黃', '小強'), ('吳', '小玲'), ('劉', '小芳'),
            ('趙', '小凱'), ('周', '小琳'), ('許', '小威'),
            ('鄭', '小雯'),
        ]
        for i, (last_name, first_name) in enumerate(emp_names, 1):
            username = f'emp{i:02d}'
            emp_user, _ = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': f'{username}@happycare.com',
                    'first_name': first_name,
                    'last_name': last_name,
                    'role': roles['employee'],
                    'organization': org,
                    'branch': branches[i % len(branches)],
                }
            )
            emp_user.set_password('emp123')
            emp_user.save()
            users[username] = emp_user

        return users

    def _create_certifications(self):
        certs = {}
        for code, name, required in [
            ('RN', '護理師執照', True),
            ('CNA', '照顧服務員', True),
            ('CPR', 'CPR證照', False),
            ('SOCIAL', '社工師執照', False),
            ('PT', '物理治療師', False),
        ]:
            cert, _ = Certification.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'description': f'{name}資格認證',
                    'is_required': required,
                }
            )
            certs[code] = cert
        return certs

    def _create_employees(self, users, org, branches, certs):
        employees = []
        emp_users = [users[f'emp{i:02d}'] for i in range(1, 11)]

        positions = ['護理師', '照顧服務員', '護理師', '照顧服務員', '社工師',
                     '護理師', '照顧服務員', '物理治療師', '護理師', '照顧服務員']
        cert_assignments = [
            ['RN', 'CPR'], ['CNA', 'CPR'], ['RN'], ['CNA'],
            ['SOCIAL', 'CPR'], ['RN', 'CPR'], ['CNA'], ['PT'],
            ['RN'], ['CNA', 'CPR']
        ]

        for i, user in enumerate(emp_users):
            emp, created = Employee.objects.get_or_create(
                user=user,
                defaults={
                    'employee_id': f'EMP{i+1:03d}',
                    'organization': org,
                    'branch': branches[i % len(branches)],
                    'position': positions[i],
                    'contract_type': 'full_time' if i < 8 else 'part_time',
                    'agreed_hours_per_week': Decimal('40.00') if i < 8 else Decimal('20.00'),
                    'hire_date': date(2024, 1, 1) + timedelta(days=i * 15),
                }
            )
            if created:
                # 新增證照
                for cert_code in cert_assignments[i]:
                    if cert_code in certs:
                        emp.certifications.add(certs[cert_code])

                # 建立合約
                Contract.objects.create(
                    employee=emp,
                    contract_type=emp.contract_type,
                    start_date=emp.hire_date,
                    base_salary=Decimal('35000.00') if emp.contract_type == 'full_time' else Decimal('20000.00'),
                    agreed_hours_per_week=emp.agreed_hours_per_week,
                )

            employees.append(emp)

        return employees

    def _create_shift_templates(self, org, certs):
        shifts = []
        for name, start, end, break_min, min_staff, cert_codes in [
            ('早班', time(8, 0), time(16, 0), 60, 3, ['RN']),
            ('中班', time(16, 0), time(0, 0), 60, 2, []),
            ('夜班', time(0, 0), time(8, 0), 60, 1, ['RN']),
            ('白班', time(8, 0), time(20, 0), 120, 2, ['CNA']),
            ('短班-早', time(8, 0), time(12, 0), 0, 1, []),
            ('短班-午', time(12, 0), time(16, 0), 0, 1, []),
        ]:
            shift, created = ShiftTemplate.objects.get_or_create(
                organization=org,
                name=name,
                defaults={
                    'start_time': start,
                    'end_time': end,
                    'break_minutes': break_min,
                    'min_staff_count': min_staff,
                    'overlap_minutes': 30,
                }
            )
            if created:
                for cert_code in cert_codes:
                    if cert_code in certs:
                        shift.required_certifications.add(certs[cert_code])
            shifts.append(shift)

        return shifts

    def _create_shift_rules(self, org):
        rules = []
        for name, rule_type, value in [
            ('最大連續工作天數', 'max_consecutive_days', {'max_days': 6}),
            ('最小休息時數', 'min_rest_hours', {'min_hours': 11}),
            ('最大每週工時', 'max_weekly_hours', {'max_hours': 40}),
            ('強制休息日', 'mandatory_rest_day', {'day': 'sunday', 'min_days_per_week': 1}),
        ]:
            rule, _ = ShiftRule.objects.get_or_create(
                organization=org,
                name=name,
                defaults={
                    'rule_type': rule_type,
                    'value': value,
                }
            )
            rules.append(rule)
        return rules

    def _create_overtime_rules(self, org):
        rules = []
        for ot_type, multiplier, max_day, max_month in [
            ('regular', '1.34', '4.00', '46.00'),
            ('rest_day', '1.34', '8.00', None),
            ('holiday', '2.00', '8.00', None),
            ('special_holiday', '2.00', '8.00', None),
        ]:
            rule, _ = OvertimeRule.objects.get_or_create(
                organization=org,
                overtime_type=ot_type,
                defaults={
                    'multiplier': Decimal(multiplier),
                    'max_hours_per_day': Decimal(max_day) if max_day else None,
                    'max_hours_per_month': Decimal(max_month) if max_month else None,
                }
            )
            rules.append(rule)
        return rules

    def _create_labor_law_rules(self):
        rules = []
        for name, rule_type, value, desc in [
            ('每週最大正常工時', 'max_weekly_hours', Decimal('40.00'),
             '依勞基法第30條，正常工作時間每週不得超過40小時'),
            ('每日最大正常工時', 'max_daily_hours', Decimal('8.00'),
             '依勞基法第30條，每日正常工作時間不得超過8小時'),
            ('兩班之間最小休息時數', 'min_rest_hours', Decimal('11.00'),
             '依勞基法第34條，兩班之間應有至少連續11小時休息'),
            ('最大連續工作天數', 'max_consecutive_days', Decimal('6.00'),
             '依勞基法第36條，每7日應有2日休息，其中1日為例假'),
            ('強制休息日', 'mandatory_rest_day', Decimal('1.00'),
             '依勞基法第36條，每7日應有1日例假'),
            ('平日延長工時倍率', 'overtime_multiplier', Decimal('1.34'),
             '依勞基法第24條，延長工時前2小時加給1/3'),
        ]:
            rule, _ = LaborLawRule.objects.get_or_create(
                name=name,
                defaults={
                    'rule_type': rule_type,
                    'value': value,
                    'description': desc,
                }
            )
            rules.append(rule)
        return rules

    def _create_sample_schedules(self, org, branches, employees, shifts, users):
        """建立範例排班表"""
        admin_user = users['admin']

        # 建立法規版排班
        today = date.today()
        month_start = today.replace(day=1)
        if today.month == 12:
            month_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            month_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

        legal_version, created = ScheduleVersion.objects.get_or_create(
            organization=org,
            version_label=f'{today.year}年{today.month}月法規版',
            version_type='legal',
            defaults={
                'branch': branches[1],
                'period_start': month_start,
                'period_end': month_end,
                'status': 'draft',
                'created_by': admin_user,
            }
        )

        count = 0
        if created:
            # 每天為前3個員工排班（早班、中班、夜班各1人）
            morning_shift = shifts[0] if len(shifts) > 0 else None
            afternoon_shift = shifts[1] if len(shifts) > 1 else None
            night_shift = shifts[2] if len(shifts) > 2 else None

            current = month_start
            while current <= month_end and current <= today + timedelta(days=14):
                for i, emp in enumerate(employees[:6]):
                    if current.weekday() == 6:  # 週日休息
                        continue
                    if i < 2 and morning_shift:
                        Schedule.objects.get_or_create(
                            schedule_version=legal_version,
                            employee=emp,
                            shift_template=morning_shift,
                            schedule_date=current,
                            defaults={'expected_hours': Decimal('7.00'), 'status': 'assigned'}
                        )
                        count += 1
                    elif i < 4 and afternoon_shift:
                        Schedule.objects.get_or_create(
                            schedule_version=legal_version,
                            employee=emp,
                            shift_template=afternoon_shift,
                            schedule_date=current,
                            defaults={'expected_hours': Decimal('7.00'), 'status': 'assigned'}
                        )
                        count += 1
                    elif night_shift:
                        Schedule.objects.get_or_create(
                            schedule_version=legal_version,
                            employee=emp,
                            shift_template=night_shift,
                            schedule_date=current,
                            defaults={'expected_hours': Decimal('7.00'), 'status': 'assigned'}
                        )
                        count += 1
                current += timedelta(days=1)

        return count
