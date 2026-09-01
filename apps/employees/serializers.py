"""
Employee serializers
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers
from .models import (
    Employee, Contract, Certification, EmployeeAvailability, EmployeeTimeSlot,
    EmployeeTag, EmployeeDataConsent,
)
from apps.accounts.models import Role
from apps.accounts.serializers import UserSerializer

User = get_user_model()


class EmployeeDataConsentSerializer(serializers.ModelSerializer):
    """員工個資使用同意紀錄。

    revoked_at 為 None 表示授權中。`is_active` 是計算欄位，前端可直接判斷
    是否需要彈出同意書。POST 不接受 revoked_at（撤回走 DELETE）。
    """
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeDataConsent
        fields = [
            'id', 'employee', 'consented_at', 'revoked_at',
            'consent_version', 'notes', 'is_active',
        ]
        read_only_fields = ['id', 'employee', 'consented_at', 'revoked_at', 'is_active']

    def get_is_active(self, obj) -> bool:
        return obj.is_active()


class EmployeeTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeTag
        fields = ['id', 'organization', 'code', 'label', 'description', 'created_at']
        read_only_fields = ['id', 'created_at']


class CertificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Certification
        fields = ['id', 'name', 'code', 'description', 'is_required', 'created_at']
        read_only_fields = ['id', 'created_at']


class ContractSerializer(serializers.ModelSerializer):
    contract_type_display = serializers.CharField(source='get_contract_type_display', read_only=True)
    
    class Meta:
        model = Contract
        fields = [
            'id', 'employee', 'contract_type', 'contract_type_display',
            'start_date', 'end_date', 'base_salary', 'agreed_hours_per_week',
            'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class EmployeeSerializer(serializers.ModelSerializer):
    user = serializers.DictField(write_only=True, required=False)
    user_id = serializers.IntegerField(write_only=True, required=False)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    contract_type_display = serializers.CharField(source='get_contract_type_display', read_only=True)
    certifications = CertificationSerializer(many=True, read_only=True)
    certification_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Certification.objects.all(),
        source='certifications',
        write_only=True,
        required=False
    )
    contracts = ContractSerializer(many=True, read_only=True)
    
    class Meta:
        model = Employee
        fields = [
            'id', 'user', 'user_id', 'employee_id', 'organization', 'organization_name',
            'branch', 'branch_name', 'position', 'contract_type', 'contract_type_display',
            'agreed_hours_per_week', 'certifications', 'certification_ids',
            'hire_date', 'is_active', 'contracts', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        user_data = attrs.get('user')
        user_id = attrs.get('user_id')

        if not self.instance and not user_data and not user_id:
            raise serializers.ValidationError({
                'user': 'User data or user_id is required.'
            })

        if user_data:
            username = user_data.get('username')
            password = user_data.get('password')
            email = user_data.get('email')

            if not username:
                raise serializers.ValidationError({'user': {'username': 'This field is required.'}})
            if not self.instance and not password:
                raise serializers.ValidationError({'user': {'password': 'This field is required.'}})

            username_qs = User.objects.filter(username=username)
            email_qs = User.objects.filter(email=email) if email else User.objects.none()
            if self.instance:
                username_qs = username_qs.exclude(pk=self.instance.user_id)
                email_qs = email_qs.exclude(pk=self.instance.user_id)
            if username_qs.exists():
                raise serializers.ValidationError({'user': {'username': 'A user with this username already exists.'}})
            if email and email_qs.exists():
                raise serializers.ValidationError({'user': {'email': 'A user with this email already exists.'}})

        organization = attrs.get(
            'organization',
            self.instance.organization if self.instance else None,
        )
        branch = attrs.get(
            'branch',
            self.instance.branch if self.instance else None,
        )
        if organization and branch and branch.organization_id != organization.id:
            raise serializers.ValidationError({
                'branch': 'Branch must belong to the selected organization.'
            })

        return attrs

    def create(self, validated_data):
        user_data = validated_data.pop('user', None)
        user_id = validated_data.pop('user_id', None)
        # M2M 不能進 objects.create()/setattr，取出後用 .set()
        certifications = validated_data.pop('certifications', None)

        with transaction.atomic():
            if user_id:
                user = User.objects.get(pk=user_id)
            else:
                password = user_data.pop('password')
                role = Role.objects.filter(name='employee').first()
                user = User(
                    username=user_data.get('username'),
                    email=user_data.get('email', ''),
                    first_name=user_data.get('first_name', ''),
                    last_name=user_data.get('last_name', ''),
                    organization=validated_data.get('organization'),
                    branch=validated_data.get('branch'),
                    role=role,
                    is_active=True,
                )
                user.set_password(password)
                user.save()

            employee = Employee.objects.create(user=user, **validated_data)
            if certifications is not None:
                employee.certifications.set(certifications)

        return employee

    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', None)
        validated_data.pop('user_id', None)
        certifications = validated_data.pop('certifications', None)

        with transaction.atomic():
            if user_data:
                password = user_data.pop('password', None)
                for field in ('username', 'email', 'first_name', 'last_name'):
                    if field in user_data:
                        setattr(instance.user, field, user_data[field])
                if 'organization' in validated_data:
                    instance.user.organization = validated_data['organization']
                if 'branch' in validated_data:
                    instance.user.branch = validated_data['branch']
                if password:
                    instance.user.set_password(password)
                instance.user.save()

            if certifications is not None:
                instance.certifications.set(certifications)
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['user'] = UserSerializer(instance.user).data
        return data


class EmployeeTimeSlotSerializer(serializers.ModelSerializer):
    slot_type_display = serializers.CharField(source='get_slot_type_display', read_only=True)
    day_of_week_display = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeTimeSlot
        fields = [
            'id', 'slot_type', 'slot_type_display',
            'day_of_week', 'day_of_week_display',
            'start_time', 'end_time', 'label', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_day_of_week_display(self, obj):
        if obj.day_of_week is None:
            return '每天'
        return obj.get_day_of_week_display()


class EmployeeAvailabilitySerializer(serializers.ModelSerializer):
    """
    員工可用性序列化器（含嵌套 time_slots）。

    PUT /employees/{id}/availability/ 時，time_slots 整筆替換：
    先刪除舊的，再批量建立新的。這樣前端只需維護一份完整清單。
    """
    time_slots = EmployeeTimeSlotSerializer(many=True, required=False)

    class Meta:
        model = EmployeeAvailability
        fields = [
            'id', 'employee',
            'required_hours_per_week',
            'special_rules',
            'effective_from', 'effective_to',
            'time_slots',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'employee', 'created_at', 'updated_at']

    def create(self, validated_data):
        slots_data = validated_data.pop('time_slots', [])
        availability = EmployeeAvailability.objects.create(**validated_data)
        for slot in slots_data:
            EmployeeTimeSlot.objects.create(availability=availability, **slot)
        return availability

    def update(self, instance, validated_data):
        slots_data = validated_data.pop('time_slots', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if slots_data is not None:
            # 整批替換：刪除舊的，建立新的
            instance.time_slots.all().delete()
            for slot in slots_data:
                EmployeeTimeSlot.objects.create(availability=instance, **slot)
        return instance


class EmployeeListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list view"""
    user = UserSerializer(read_only=True)
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    certification_count = serializers.IntegerField(source='certifications.count', read_only=True)
    
    class Meta:
        model = Employee
        fields = [
            'id', 'employee_id', 'user', 'user_name', 'user_email',
            'organization', 'branch',
            'organization_name', 'branch_name', 'position',
            'contract_type', 'is_active', 'certification_count', 'hire_date'
        ]
