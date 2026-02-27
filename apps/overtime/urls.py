"""
Overtime URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import OvertimeRecordViewSet, OvertimeRuleViewSet

router = DefaultRouter()
router.register(r'records', OvertimeRecordViewSet, basename='overtime-record')
router.register(r'rules', OvertimeRuleViewSet, basename='overtime-rule')

urlpatterns = [
    path('', include(router.urls)),
]
