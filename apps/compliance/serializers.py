"""
Compliance serializers
"""
from rest_framework import serializers
from .models import LaborLawRule, ComplianceCheck, OrgComplianceSettings


# Rule keys the engine understands; the settings endpoint validates against this.
VALID_RULE_TYPES = {
    'max_weekly_hours', 'max_consecutive_days', 'min_rest_hours', 'max_daily_hours',
}


class OrgComplianceSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrgComplianceSettings
        fields = ['id', 'organization', 'soft_rule_types', 'weekly_closed_days',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'organization', 'created_at', 'updated_at']

    def validate_weekly_closed_days(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('weekly_closed_days must be a list')
        if any(not isinstance(d, int) or d < 0 or d > 6 for d in value):
            raise serializers.ValidationError(
                'weekly_closed_days entries must be integers 0(Mon)–6(Sun)'
            )
        if len(value) >= 7:
            raise serializers.ValidationError('cannot close every day of the week')
        return sorted(set(value))

    def validate_soft_rule_types(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('soft_rule_types must be a list')
        unknown = set(value) - VALID_RULE_TYPES
        if unknown:
            raise serializers.ValidationError(
                f'unknown rule types: {sorted(unknown)}; '
                f'valid: {sorted(VALID_RULE_TYPES)}'
            )
        return value


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


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        from .models import Holiday
        model = Holiday
        fields = ['id', 'organization', 'date', 'name', 'created_at']
        read_only_fields = ['id', 'created_at']
