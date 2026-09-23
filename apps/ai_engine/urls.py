"""
AI Engine URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AIEngineViewSet, LLMScheduleViewSet

router = DefaultRouter()
router.register(r'schedule', AIEngineViewSet, basename='ai-schedule')
router.register(r'schedule', LLMScheduleViewSet, basename='ai-llm-schedule')

urlpatterns = [
    path('', include(router.urls)),
]
