"""
Employee URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EmployeeViewSet, ContractViewSet, CertificationViewSet

router = DefaultRouter()
router.register(r'employees', EmployeeViewSet, basename='employee')
router.register(r'contracts', ContractViewSet, basename='contract')
router.register(r'certifications', CertificationViewSet, basename='certification')

urlpatterns = [
    path('', include(router.urls)),
]
