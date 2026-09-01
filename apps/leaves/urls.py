"""
Leave URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LeaveRequestViewSet, LeaveSettingsView, LeaveBalancesView

router = DefaultRouter()
router.register(r'requests', LeaveRequestViewSet, basename='leave-request')

urlpatterns = [
    path('settings/', LeaveSettingsView.as_view({'get': 'list', 'put': 'put'}), name='leave-settings'),
    path('balances/', LeaveBalancesView.as_view({'get': 'list'}), name='leave-balances'),
    path('', include(router.urls)),
]
