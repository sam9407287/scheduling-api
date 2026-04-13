"""
Shift views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Q
from .models import ShiftTemplate, ShiftRule, ShiftEmployeePriority
from .serializers import ShiftTemplateSerializer, ShiftRuleSerializer, ShiftEmployeePrioritySerializer
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

    @action(detail=True, methods=['get', 'put'])
    def employee_priorities(self, request, pk=None):
        """
        取得或整批替換班別的員工優先順序清單。

        GET → 回傳排序清單
        PUT → 整批替換（先刪除舊的，再建立新的）
              傳入格式：[{"employee": <id>, "priority_rank": 1, "max_extra_shifts": null}, ...]
        """
        shift = self.get_object()

        if request.method == 'GET':
            priorities = shift.employee_priorities.select_related('employee__user').all()
            return Response(ShiftEmployeePrioritySerializer(priorities, many=True).data)

        # PUT — bulk replace
        serializer = ShiftEmployeePrioritySerializer(data=request.data, many=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            shift.employee_priorities.all().delete()
            for item in serializer.validated_data:
                ShiftEmployeePriority.objects.create(shift_template=shift, **item)

        priorities = shift.employee_priorities.select_related('employee__user').all()
        return Response(ShiftEmployeePrioritySerializer(priorities, many=True).data)


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
