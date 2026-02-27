"""
URL configuration for scheduling-api project.
"""
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    
    # API endpoints
    path('api/auth/', include('apps.accounts.urls')),
    path('api/organizations/', include('apps.organizations.urls')),
    path('api/employees/', include('apps.employees.urls')),
    path('api/shifts/', include('apps.shifts.urls')),
    path('api/schedules/', include('apps.schedules.urls')),
    path('api/attendance/', include('apps.attendance.urls')),
    path('api/overtime/', include('apps.overtime.urls')),
    path('api/compliance/', include('apps.compliance.urls')),
    path('api/ai/', include('apps.ai_engine.urls')),
]
