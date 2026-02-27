"""
Compliance serializers
"""
from rest_framework import serializers
from .models import LaborLawRule, ComplianceCheck


class LaborLawRuleSerializer(serializers.ModelSerializer):
    rule_type_display = serializers.CharField(source='get_rule_type_display', read_only=True)
    
    class Meta:
        model = LaborLawRule
        fields = [
            'id', 'name', 'rule_type', 'rule_type_display',
            'value', 'description', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ComplianceCheckSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    check_type_display = serializers.CharField(source='get_check_type_display', read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    checked_by_name = serializers.CharField(source='checked_by.username', read_only=True)
    
    class Meta:
        model = ComplianceCheck
        fields = [
            'id', 'organization', 'organization_name', 'check_type', 'check_type_display',
            'check_period_start', 'check_period_end', 'status', 'status_display',
            'violations', 'warnings', 'checked_by', 'checked_by_name',
            'checked_at', 'notes'
        ]
        read_only_fields = ['id', 'checked_at']
