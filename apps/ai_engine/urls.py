"""
AI Engine URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AIEngineViewSet

router = DefaultRouter()
router.register(r'schedule', AIEngineViewSet, basename='ai-schedule')

urlpatterns = [
    path('', include(router.urls)),
]
