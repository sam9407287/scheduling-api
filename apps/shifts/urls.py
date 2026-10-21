"""
Shift URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ShiftTemplateViewSet, ShiftRuleViewSet, TeamConstraintViewSet,
    StaffingRequirementViewSet,
)

router = DefaultRouter()
router.register(r'templates', ShiftTemplateViewSet, basename='shift-template')
router.register(r'rules', ShiftRuleViewSet, basename='shift-rule')
router.register(r'team-constraints', TeamConstraintViewSet, basename='team-constraint')
router.register(r'staffing-requirements', StaffingRequirementViewSet, basename='staffing-requirement')

urlpatterns = [
    path('', include(router.urls)),
]
