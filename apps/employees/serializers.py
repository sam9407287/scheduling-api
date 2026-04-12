"""
Employee serializers
"""
from rest_framework import serializers
from .models import Employee, Contract, Certification, EmployeeAvailability, EmployeeTimeSlot
from apps.accounts.serializers import UserSerializer


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
    user = UserSerializer(read_only=True)
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
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    certification_count = serializers.IntegerField(source='certifications.count', read_only=True)
    
    class Meta:
        model = Employee
        fields = [
            'id', 'employee_id', 'user_name', 'user_email',
            'organization_name', 'branch_name', 'position',
            'contract_type', 'is_active', 'certification_count', 'hire_date'
        ]
