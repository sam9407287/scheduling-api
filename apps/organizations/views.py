"""
Organization views
"""
import secrets

from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Organization, Branch
from .serializers import OrganizationSerializer, BranchSerializer
from apps.accounts.permissions import IsManager


class OrganizationViewSet(viewsets.ModelViewSet):
    """Organization management — self-service tenants (2026-09-23).

    Google 登入不再自動開機構：新 manager 進系統後自己 POST 建立，
    建立的機構自動綁定為該帳號的租戶。每個帳號只看得到自己的機構
    （superuser 例外），一個帳號限一間（單一 organization FK）。
    """
    queryset = Organization.objects.prefetch_related('branches').all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsManager]
    search_fields = ['name', 'code']
    ordering_fields = ['name', 'created_at']

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(pk=self.request.user.organization_id)

    def create(self, request, *args, **kwargs):
        user = request.user
        if not user.is_superuser and user.organization_id:
            return Response(
                {'code': 'organization_already_exists',
                 'error': '此帳號已有機構，一個帳號只能建立一間機構。'},
                status=status.HTTP_409_CONFLICT,
            )
        data = request.data.copy()
        if not data.get('code'):
            data['code'] = f'ORG-{secrets.token_hex(4).upper()}'
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        if not user.is_superuser:
            user.organization = organization
            user.save(update_fields=['organization'])
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return Response(
                {'code': 'forbidden', 'error': '只有系統管理員可以刪除機構。'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)


class BranchViewSet(viewsets.ModelViewSet):
    """Branch management"""
    queryset = Branch.objects.select_related('organization').all()
    serializer_class = BranchSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ['name', 'code', 'organization__name']
    ordering_fields = ['name', 'created_at']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by organization
        org_id = self.request.query_params.get('organization')
        if org_id:
            queryset = queryset.filter(organization_id=org_id)
        
        # Filter by organization if user is not admin
        # (organization=None matches nothing — org-less users see an empty list)
        if not self.request.user.is_superuser:
            queryset = queryset.filter(organization=self.request.user.organization)

        return queryset
