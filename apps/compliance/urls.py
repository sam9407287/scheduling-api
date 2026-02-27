"""
Compliance URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LaborLawRuleViewSet, ComplianceCheckViewSet

router = DefaultRouter()
router.register(r'rules', LaborLawRuleViewSet, basename='labor-law-rule')
router.register(r'checks', ComplianceCheckViewSet, basename='compliance-check')

urlpatterns = [
    path('', include(router.urls)),
]
