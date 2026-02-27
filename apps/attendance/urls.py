"""
Attendance URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AttendanceViewSet, AnomalyRecordViewSet

router = DefaultRouter()
router.register(r'attendances', AttendanceViewSet, basename='attendance')
router.register(r'anomalies', AnomalyRecordViewSet, basename='anomaly')

urlpatterns = [
    path('', include(router.urls)),
]
