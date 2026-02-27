"""
Shift serializers
"""
from rest_framework import serializers
from .models import ShiftTemplate, ShiftRule
from apps.employees.models import Certification
from apps.employees.serializers import CertificationSerializer


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
