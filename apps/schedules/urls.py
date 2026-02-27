"""
Schedule URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ScheduleViewSet, ScheduleVersionViewSet, ScheduleChangeViewSet

router = DefaultRouter()
router.register(r'versions', ScheduleVersionViewSet, basename='schedule-version')
router.register(r'schedules', ScheduleViewSet, basename='schedule')
router.register(r'changes', ScheduleChangeViewSet, basename='schedule-change')

urlpatterns = [
    path('', include(router.urls)),
]
