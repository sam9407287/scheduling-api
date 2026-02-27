"""
Organization serializers
"""
from rest_framework import serializers
from .models import Organization, Branch


class BranchSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    
    class Meta:
        model = Branch
        fields = [
            'id', 'organization', 'organization_name', 'name', 'code',
            'address', 'phone', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class OrganizationSerializer(serializers.ModelSerializer):
    branches = BranchSerializer(many=True, read_only=True)
    branch_count = serializers.IntegerField(source='branches.count', read_only=True)
    
    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'code', 'address', 'phone', 'email',
            'is_active', 'branches', 'branch_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
