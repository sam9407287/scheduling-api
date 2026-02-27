"""
Organization views
"""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Organization, Branch
from .serializers import OrganizationSerializer, BranchSerializer
from apps.accounts.permissions import IsManager


class OrganizationViewSet(viewsets.ModelViewSet):
    """Organization management"""
    queryset = Organization.objects.prefetch_related('branches').all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsManager]
    search_fields = ['name', 'code']
    ordering_fields = ['name', 'created_at']


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
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(organization=self.request.user.organization)
        
        return queryset
