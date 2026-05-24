"""
Shift serializers
"""
from rest_framework import serializers
from .models import ShiftTemplate, ShiftRule, ShiftEmployeePriority, TeamConstraint
from apps.employees.models import Certification
from apps.employees.serializers import CertificationSerializer


class TeamConstraintSerializer(serializers.ModelSerializer):
    """
    Team constraint = Notion-filter-style rule (scope × condition × quantifier).

    `condition_value` shape depends on `condition_type` — see
    docs/PHASE_1_FRONTEND_GUIDE.md §6 for the matrix. The serializer does
    light shape validation but trusts the frontend builder to enforce the
    rest (e.g. tag/cert ids must exist).
    """
    condition_type_display = serializers.CharField(
        source='get_condition_type_display', read_only=True
    )
    condition_operator_display = serializers.CharField(
        source='get_condition_operator_display', read_only=True
    )
    quantifier_display = serializers.CharField(
        source='get_quantifier_display', read_only=True
    )

    class Meta:
        model = TeamConstraint
        fields = [
            'id', 'organization', 'branch', 'shift_template',
            'scope_time_of_day',
            'condition_type', 'condition_type_display',
            'condition_operator', 'condition_operator_display',
            'condition_value',
            'quantifier', 'quantifier_display', 'quantity',
            'description', 'severity', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, data):
        # Shape sanity per condition_type — keep simple, the builder UI
        # carries the heavier validation.
        ctype = data.get('condition_type', getattr(self.instance, 'condition_type', None))
        cval = data.get('condition_value',
                        getattr(self.instance, 'condition_value', None))
        if ctype in ('tag', 'certification') and cval is not None:
            if not isinstance(cval, list):
                raise serializers.ValidationError({
                    'condition_value': f'{ctype} requires a list value'
                })
        if ctype in ('height_cm', 'weight_kg', 'age_years') and cval is not None:
            if not isinstance(cval, (int, float)):
                raise serializers.ValidationError({
                    'condition_value': f'{ctype} requires a numeric value'
                })
        if data.get('quantity', getattr(self.instance, 'quantity', 1)) < 0:
            raise serializers.ValidationError({'quantity': 'must be ≥ 0'})
        return data


class ShiftRuleSerializer(serializers.ModelSerializer):
    rule_type_display = serializers.CharField(source='get_rule_type_display', read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)

    class Meta:
        model = ShiftRule
        fields = [
            'id', 'organization', 'organization_name', 'name', 'rule_type',
            'rule_type_display', 'value', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ShiftEmployeePrioritySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.user.get_full_name', read_only=True)

    class Meta:
        model = ShiftEmployeePriority
        fields = ['id', 'employee', 'employee_name', 'priority_rank', 'max_extra_shifts']
        read_only_fields = ['id']


class ShiftTemplateSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    required_certifications = CertificationSerializer(many=True, read_only=True)
    certification_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Certification.objects.all(),
        source='required_certifications',
        write_only=True,
        required=False
    )
    duration_hours = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)

    class Meta:
        model = ShiftTemplate
        fields = [
            'id', 'organization', 'organization_name', 'name',
            'start_time', 'end_time', 'break_minutes', 'overlap_minutes',
            'min_staff_count', 'required_certifications', 'certification_ids',
            'duration_hours', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'duration_hours', 'created_at', 'updated_at']
