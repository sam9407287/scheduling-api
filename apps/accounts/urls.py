"""
Account URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    UserViewSet, RoleViewSet, LoginView, RegisterSendCodeView, RegisterView,
)

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'roles', RoleViewSet, basename='role')

urlpatterns = [
    path('login/', LoginView.as_view(), name='login'),
    path('register/send-code/', RegisterSendCodeView.as_view(), name='register-send-code'),
    path('register/', RegisterView.as_view(), name='register'),
    path('', include(router.urls)),
]
