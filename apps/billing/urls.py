"""
Billing URLs.

  /api/billing/rates/            (ReadOnly ViewSet — list/detail)
  /api/billing/usage/            (UsageView GET)
  /api/billing/settings/         (OrgBillingSettingsView GET/PATCH)
  /api/billing/estimate/         (EstimateView POST)
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    BillingRateConfigViewSet, UsageView, OrgBillingSettingsView, EstimateView,
)

router = DefaultRouter()
router.register(r'rates', BillingRateConfigViewSet, basename='billing-rate')

urlpatterns = [
    path('', include(router.urls)),
    path('usage/', UsageView.as_view(), name='billing-usage'),
    path('settings/', OrgBillingSettingsView.as_view(), name='billing-settings'),
    path('estimate/', EstimateView.as_view(), name='billing-estimate'),
]
