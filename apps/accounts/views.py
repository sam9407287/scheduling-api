"""
Account views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model
from .models import Role
from .serializers import UserSerializer, UserProfileSerializer, RoleSerializer
from .permissions import IsAdmin, IsManager

User = get_user_model()


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
