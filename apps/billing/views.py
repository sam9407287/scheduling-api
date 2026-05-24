"""
Billing API surface.

  GET    /api/billing/rates/                  → all rate configs (public)
  GET    /api/billing/usage/?year=&month=     → period summary + recent records
  GET    /api/billing/settings/               → caller-org settings
  PATCH  /api/billing/settings/               → update monthly cap etc
  POST   /api/billing/estimate/               → dry-run estimate for a request
"""
from datetime import date as _date
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from django.utils import timezone
from .models import (
    BillingRateConfig, OrgBillingSettings, BillingPeriod, UsageRecord,
    estimate_tokens, would_exceed_cap, BILLING_MODES,
)
from .serializers import (
    BillingRateConfigSerializer, OrgBillingSettingsSerializer,
    UsageRecordSerializer, BillingPeriodSerializer,
)
from apps.accounts.permissions import IsManager


class BillingRateConfigViewSet(viewsets.ReadOnlyModelViewSet):
    """Customers can see the current rate table. Mutations go via Django admin."""
    queryset = BillingRateConfig.objects.all()
    serializer_class = BillingRateConfigSerializer
    permission_classes = [IsAuthenticated]


class UsageView(APIView):
    """`GET /api/billing/usage/?year=Y&month=M` — defaults to current month."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = request.user.organization
        if not org and not request.user.is_superuser:
            return Response(
                {'error': 'user has no organization'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Superusers may inspect any org via ?organization=.
        if request.user.is_superuser and request.query_params.get('organization'):
            from apps.organizations.models import Organization
            try:
                org = Organization.objects.get(
                    pk=request.query_params['organization']
                )
            except Organization.DoesNotExist:
                return Response({'error': 'organization not found'},
                                status=status.HTTP_404_NOT_FOUND)

        now = timezone.now()
        try:
            year = int(request.query_params.get('year', now.year))
            month = int(request.query_params.get('month', now.month))
        except (TypeError, ValueError):
            return Response({'error': 'year/month must be integers'},
                            status=status.HTTP_400_BAD_REQUEST)

        period, _ = BillingPeriod.objects.get_or_create(
            organization=org, period_year=year, period_month=month,
            defaults={'status': 'open', 'total_tokens': 0},
        )
        records = (
            UsageRecord.objects
            .filter(billing_period=period)
            .order_by('-created_at')[:100]
        )
        settings_row = OrgBillingSettings.objects.filter(organization=org).first()
        cap = settings_row.monthly_cap_tokens if settings_row else None

        return Response({
            'organization_id': org.id,
            'period': BillingPeriodSerializer(period).data,
            'cap': cap,
            'cap_pct_used': (
                round(100 * period.total_tokens / cap, 2)
                if cap else None
            ),
            'records': UsageRecordSerializer(records, many=True).data,
        })


class OrgBillingSettingsView(APIView):
    """GET / PATCH the caller-org's billing settings (Manager only)."""
    permission_classes = [IsManager]

    def _settings_for_caller(self, request):
        org = request.user.organization
        if not org:
            return None, Response(
                {'error': 'user has no organization'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        settings_row, _ = OrgBillingSettings.objects.get_or_create(
            organization=org,
        )
        return settings_row, None

    def get(self, request):
        settings_row, err = self._settings_for_caller(request)
        if err:
            return err
        return Response(OrgBillingSettingsSerializer(settings_row).data)

    def patch(self, request):
        settings_row, err = self._settings_for_caller(request)
        if err:
            return err
        serializer = OrgBillingSettingsSerializer(
            settings_row, data=request.data, partial=True,
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data)


class EstimateView(APIView):
    """
    `POST /api/billing/estimate/` — dry run, returns projected token cost
    and whether it would exceed the current monthly cap.

    Body: { billing_mode: "generate" | "fill_gaps" | "derive_legal" }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        org = request.user.organization
        if not org:
            return Response(
                {'error': 'user has no organization'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        billing_mode = request.data.get('billing_mode')
        if billing_mode not in dict(BILLING_MODES):
            return Response(
                {'error': f'billing_mode must be one of {list(dict(BILLING_MODES))}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tokens = estimate_tokens(billing_mode, request.data.get('request_metadata') or {})
        exceeds, current, projected, cap = would_exceed_cap(org, billing_mode)
        return Response({
            'billing_mode': billing_mode,
            'tokens_to_charge': tokens,
            'current_period_tokens': current,
            'projected_period_tokens': projected,
            'monthly_cap_tokens': cap,
            'would_exceed_cap': exceeds,
        })
