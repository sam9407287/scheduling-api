"""
Employee views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import Employee, Contract, Certification
from .serializers import (
    EmployeeSerializer,
    EmployeeListSerializer,
    ContractSerializer,
    CertificationSerializer
)
from apps.accounts.permissions import IsManager, IsSupervisor


class CertificationViewSet(viewsets.ModelViewSet):
    """Certification management"""
    queryset = Certification.objects.all()
    serializer_class = CertificationSerializer
    permission_classes = [IsManager]
    search_fields = ['name', 'code']
    ordering_fields = ['name', 'created_at']


class ContractViewSet(viewsets.ModelViewSet):
    """Contract management"""
    queryset = Contract.objects.all()
    serializer_class = ContractSerializer
    permission_classes = [IsSupervisor]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by employee if specified
        employee_id = self.request.query_params.get('employee_id')
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by organization if user is not admin
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(employee__organization=self.request.user.organization)
        
        return queryset.order_by('-start_date')


class EmployeeViewSet(viewsets.ModelViewSet):
    """Employee management"""
    queryset = Employee.objects.select_related('user', 'organization', 'branch').prefetch_related('certifications', 'contracts')
    permission_classes = [IsSupervisor]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return EmployeeListSerializer
        return EmployeeSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by organization if user is not admin
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(organization=self.request.user.organization)
            
            # Filter by branch if user has branch
            if self.request.user.branch:
                queryset = queryset.filter(branch=self.request.user.branch)
        
        # Search
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(employee_id__icontains=search) |
                Q(user__username__icontains=search) |
                Q(user__email__icontains=search) |
                Q(user__first_name__icontains=search) |
                Q(user__last_name__icontains=search)
            )
        
        # Filter by is_active
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Filter by organization
        org_id = self.request.query_params.get('organization')
        if org_id:
            queryset = queryset.filter(organization_id=org_id)
        
        # Filter by branch
        branch_id = self.request.query_params.get('branch')
        if branch_id:
            queryset = queryset.filter(branch_id=branch_id)
        
        # Filter by certification
        cert_id = self.request.query_params.get('certification')
        if cert_id:
            queryset = queryset.filter(certifications__id=cert_id)
        
        return queryset.order_by('employee_id')
    
    @action(detail=True, methods=['get'])
    def contracts(self, request, pk=None):
        """Get employee contracts"""
        employee = self.get_object()
        contracts = employee.contracts.all()
        serializer = ContractSerializer(contracts, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def add_contract(self, request, pk=None):
        """Add contract to employee"""
        employee = self.get_object()
        serializer = ContractSerializer(data={**request.data, 'employee': employee.id})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['get'])
    def certifications(self, request, pk=None):
        """Get employee certifications"""
        employee = self.get_object()
        certifications = employee.certifications.all()
        serializer = CertificationSerializer(certifications, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def add_certification(self, request, pk=None):
        """Add certification to employee"""
        employee = self.get_object()
        cert_id = request.data.get('certification_id')
        if not cert_id:
            return Response(
                {'error': 'certification_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            cert = Certification.objects.get(id=cert_id)
            employee.certifications.add(cert)
            return Response({'message': 'Certification added'}, status=status.HTTP_200_OK)
        except Certification.DoesNotExist:
            return Response(
                {'error': 'Certification not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=True, methods=['delete'])
    def remove_certification(self, request, pk=None):
        """Remove certification from employee"""
        employee = self.get_object()
        cert_id = request.data.get('certification_id')
        if not cert_id:
            return Response(
                {'error': 'certification_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            cert = Certification.objects.get(id=cert_id)
            employee.certifications.remove(cert)
            return Response({'message': 'Certification removed'}, status=status.HTTP_200_OK)
        except Certification.DoesNotExist:
            return Response(
                {'error': 'Certification not found'},
                status=status.HTTP_404_NOT_FOUND
            )
