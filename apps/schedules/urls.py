"""
Schedule URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ScheduleViewSet,
    ScheduleVersionViewSet,
    ScheduleChangeViewSet,
    ScheduleCellAcknowledgmentViewSet,
)

router = DefaultRouter()
router.register(r'versions', ScheduleVersionViewSet, basename='schedule-version')
router.register(r'schedules', ScheduleViewSet, basename='schedule')
router.register(r'changes', ScheduleChangeViewSet, basename='schedule-change')
router.register(
    r'cell-acknowledgments',
    ScheduleCellAcknowledgmentViewSet,
    basename='cell-acknowledgment',
)

urlpatterns = [
    path(
        'approved-timeline/',
        ScheduleVersionViewSet.as_view({'get': 'approved_timeline'}),
        name='approved-timeline',
    ),
    path(
        'day-overview/',
        ScheduleVersionViewSet.as_view({'get': 'day_overview'}),
        name='day-overview',
    ),
    path('', include(router.urls)),
]
