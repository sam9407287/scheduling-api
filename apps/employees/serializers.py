"""
Employee serializers
"""
from rest_framework import serializers
from .models import Employee, Contract, Certification
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
