"""
Billing serializers (read-only over the metered models for Phase 2).
"""
from rest_framework import serializers
from .models import (
    BillingRateConfig, OrgBillingSettings, BillingPeriod, UsageRecord,
)


class BillingRateConfigSerializer(serializers.ModelSerializer):
    billing_mode_display = serializers.CharField(
        source='get_billing_mode_display', read_only=True
    )

    class Meta:
        model = BillingRateConfig
        fields = [
            'id', 'billing_mode', 'billing_mode_display',
            'tokens_per_call', 'effective_from', 'notes',
        ]
        read_only_fields = fields  # rates are admin-managed only


class OrgBillingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrgBillingSettings
        fields = [
            'id', 'organization',
            'monthly_cap_tokens', 'alert_threshold_pct', 'billing_email',
            'is_billing_enabled', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'organization', 'created_at', 'updated_at']


class UsageRecordSerializer(serializers.ModelSerializer):
    billing_mode_display = serializers.CharField(
        source='get_billing_mode_display', read_only=True
    )
    solver_status_display = serializers.CharField(
        source='get_solver_status_display', read_only=True
    )

    class Meta:
        model = UsageRecord
        fields = [
            'id', 'organization', 'billing_period',
            'billing_mode', 'billing_mode_display',
            'tokens_charged',
            'solver_status', 'solver_status_display',
            'schedule_version', 'user',
            'request_metadata', 'created_at',
        ]
        read_only_fields = fields


class BillingPeriodSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(
        source='get_status_display', read_only=True
    )

    class Meta:
        model = BillingPeriod
        fields = [
            'id', 'organization', 'period_year', 'period_month',
            'total_tokens', 'status', 'status_display',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields
