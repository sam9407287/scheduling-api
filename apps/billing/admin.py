from django.contrib import admin
from .models import (
    BillingRateConfig, OrgBillingSettings, BillingPeriod, UsageRecord,
    PaymentMethod, BillingAlert,
)


@admin.register(BillingAlert)
class BillingAlertAdmin(admin.ModelAdmin):
    list_display = ['organization', 'billing_period', 'threshold_pct',
                    'tokens_at_alert', 'recipient', 'sent_at']
    list_filter = ['threshold_pct', 'sent_at']
    search_fields = ['organization__name', 'organization__code']
    readonly_fields = ['organization', 'billing_period', 'threshold_pct',
                       'tokens_at_alert', 'recipient', 'sent_at']


@admin.register(BillingRateConfig)
class BillingRateConfigAdmin(admin.ModelAdmin):
    list_display = ['billing_mode', 'tokens_per_call', 'effective_from', 'notes']
    list_filter = ['billing_mode']
    date_hierarchy = 'effective_from'


@admin.register(OrgBillingSettings)
class OrgBillingSettingsAdmin(admin.ModelAdmin):
    list_display = ['organization', 'monthly_cap_tokens', 'alert_threshold_pct',
                    'is_billing_enabled']
    list_filter = ['is_billing_enabled']
    search_fields = ['organization__name', 'organization__code']


@admin.register(BillingPeriod)
class BillingPeriodAdmin(admin.ModelAdmin):
    list_display = ['organization', 'period_year', 'period_month',
                    'total_tokens', 'status']
    list_filter = ['status', 'period_year']
    search_fields = ['organization__name', 'organization__code']


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = ['organization', 'billing_mode', 'tokens_charged',
                    'solver_status', 'created_at']
    list_filter = ['billing_mode', 'solver_status']
    date_hierarchy = 'created_at'
    search_fields = ['organization__name', 'organization__code']
    readonly_fields = ['organization', 'billing_period', 'billing_mode',
                       'tokens_charged', 'solver_status',
                       'schedule_version', 'user', 'request_metadata',
                       'created_at']


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ['organization', 'provider', 'brand', 'last_4', 'is_active']
    list_filter = ['provider', 'is_active']
    search_fields = ['organization__name', 'organization__code']
