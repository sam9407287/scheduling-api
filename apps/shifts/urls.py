"""
Shift URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ShiftTemplateViewSet, ShiftRuleViewSet

router = DefaultRouter()
router.register(r'templates', ShiftTemplateViewSet, basename='shift-template')
router.register(r'rules', ShiftRuleViewSet, basename='shift-rule')

urlpatterns = [
    path('', include(router.urls)),
]
