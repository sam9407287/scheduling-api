"""
Account views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema
from django.contrib.auth import get_user_model, authenticate
from .models import Role
from .serializers import (
    UserSerializer, UserProfileSerializer, RoleSerializer,
    LoginSerializer, LoginResponseSerializer,
)
from .permissions import IsAdmin, IsManager

User = get_user_model()


class LoginView(APIView):
    """Session/Token login for development & testing"""
    permission_classes = [AllowAny]

    @extend_schema(
        request=LoginSerializer,
        responses={200: LoginResponseSerializer},
        summary='登入取得 Token',
        description='使用帳號密碼登入，取得認證 Token（僅用於本地開發/測試）',
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        username = serializer.validated_data['username']
        password = serializer.validated_data['password']

        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response(
                {'error': '帳號或密碼錯誤'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Try Token auth first
        try:
            from rest_framework.authtoken.models import Token
            token, _ = Token.objects.get_or_create(user=user)
            return Response({
                'token': token.key,
                'user': UserProfileSerializer(user).data,
            })
        except Exception:
            # Fallback: session login
            from django.contrib.auth import login
            login(request, user)
            return Response({
                'message': '登入成功 (session)',
                'user': UserProfileSerializer(user).data,
            })


class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    """Role API (read-only)"""
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated]


class UserViewSet(viewsets.ModelViewSet):
    """User management API"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsManager]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by organization if user is not admin
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(organization=self.request.user.organization)
        
        return queryset
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def me(self, request):
        """Get current user profile"""
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)
    
    @action(detail=False, methods=['patch'], permission_classes=[IsAuthenticated])
    def update_profile(self, request):
        """Update current user profile"""
        serializer = UserProfileSerializer(
            request.user,
            data=request.data,
            partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
