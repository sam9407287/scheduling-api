"""
Compliance URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    LaborLawRuleViewSet, ComplianceCheckViewSet, OrgComplianceSettingsView,
)

router = DefaultRouter()
router.register(r'rules', LaborLawRuleViewSet, basename='labor-law-rule')
router.register(r'checks', ComplianceCheckViewSet, basename='compliance-check')

urlpatterns = [
    path('settings/', OrgComplianceSettingsView.as_view(), name='compliance-settings'),
    path('', include(router.urls)),
]
