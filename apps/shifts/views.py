"""
Shift views
"""
from rest_framework import viewsets
from django.db.models import Q
from .models import ShiftTemplate, ShiftRule
from .serializers import ShiftTemplateSerializer, ShiftRuleSerializer
from apps.accounts.permissions import IsManager, IsSupervisor


class ShiftTemplateViewSet(viewsets.ModelViewSet):
    """Shift template management"""
    queryset = ShiftTemplate.objects.select_related('organization').prefetch_related('required_certifications')
    serializer_class = ShiftTemplateSerializer
    permission_classes = [IsSupervisor]
    search_fields = ['name', 'organization__name']
    ordering_fields = ['name', 'start_time', 'created_at']
    
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
        
        # Filter by is_active
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        return queryset


class ShiftRuleViewSet(viewsets.ModelViewSet):
    """Shift rule management"""
    queryset = ShiftRule.objects.select_related('organization').all()
    serializer_class = ShiftRuleSerializer
    permission_classes = [IsManager]
    search_fields = ['name', 'organization__name']
    ordering_fields = ['name', 'rule_type', 'created_at']
    
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
        
        # Filter by rule_type
        rule_type = self.request.query_params.get('rule_type')
        if rule_type:
            queryset = queryset.filter(rule_type=rule_type)
        
        # Filter by is_active
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        return queryset
