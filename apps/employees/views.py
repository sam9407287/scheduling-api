"""
Employee views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django.utils import timezone
from .models import (
    Employee, Contract, Certification, EmployeeAvailability,
    EmployeeDataConsent, EmployeeTag,
)
from .serializers import (
    EmployeeSerializer,
    EmployeeListSerializer,
    ContractSerializer,
    CertificationSerializer,
    EmployeeAvailabilitySerializer,
    EmployeeTimeSlotSerializer,
    EmployeeDataConsentSerializer,
    EmployeeTagSerializer,
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

    def get_permissions(self):
        # data-consent must be reachable by the employee themselves (PDPA
        # self-consent). The action body still enforces self-only for
        # POST/DELETE; supervisors can GET to audit.
        if self.action == 'data_consent':
            return [IsAuthenticated()]
        return super().get_permissions()

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
    
    @action(detail=True, methods=['get', 'put', 'patch'])
    def availability(self, request, pk=None):
        """
        取得或更新員工可用性設定（含所有 time_slots）。

        GET  → 回傳現有設定（若尚未建立回傳 204）
        PUT  → 完整建立或替換（time_slots 整批替換）
        PATCH → 部分更新（time_slots 若有傳入則整批替換，未傳入則不動）
        """
        employee = self.get_object()

        if request.method == 'GET':
            try:
                avail = employee.availability
            except EmployeeAvailability.DoesNotExist:
                return Response(
                    {'detail': 'No availability config yet. Use PUT to create one.'},
                    status=status.HTTP_204_NO_CONTENT,
                )
            return Response(EmployeeAvailabilitySerializer(avail).data)

        # PUT / PATCH
        try:
            avail = employee.availability
            partial = (request.method == 'PATCH')
            serializer = EmployeeAvailabilitySerializer(avail, data=request.data, partial=partial)
        except EmployeeAvailability.DoesNotExist:
            # 尚未建立，自動建立
            serializer = EmployeeAvailabilitySerializer(
                data={**request.data, 'employee': employee.id}
            )

        if serializer.is_valid():
            serializer.save(employee=employee)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='availability/time_slots')
    def add_time_slot(self, request, pk=None):
        """
        對指定員工新增單一 time_slot（不替換現有）。
        適合前端「新增一筆時段」按鈕。
        """
        employee = self.get_object()
        avail, _ = EmployeeAvailability.objects.get_or_create(employee=employee)
        serializer = EmployeeTimeSlotSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(availability=avail)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(
        detail=True,
        methods=['delete'],
        url_path=r'availability/time_slots/(?P<slot_id>\d+)',
    )
    def remove_time_slot(self, request, pk=None, slot_id=None):
        """刪除指定員工的單一 time_slot。"""
        employee = self.get_object()
        try:
            slot = employee.availability.time_slots.get(id=slot_id)
        except (EmployeeAvailability.DoesNotExist, Exception):
            return Response({'error': 'Time slot not found'}, status=status.HTTP_404_NOT_FOUND)
        slot.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='data-consent')
    def data_consent(self, request, pk=None):
        """
        個資使用同意紀錄。

        GET     → 查當前狀態（無紀錄回 204；有則回完整 payload）
        POST    → 員工首次同意（建立 EmployeeDataConsent）；若已存在但已撤回
                  則重新啟用（清掉 revoked_at + 更新 consent_version）
        DELETE  → 撤回（設 revoked_at = now，保留 row 供稽核）

        權限：員工只能操作自己；supervisor 以上可 GET 任何人，但 POST 必須由
        員工本人發起（避免雇主代簽，違反 PDPA 自主原則）。
        """
        employee = self.get_object()
        is_self = (request.user.is_authenticated
                   and employee.user_id == request.user.id)

        if request.method == 'GET':
            try:
                consent = employee.data_consent
            except EmployeeDataConsent.DoesNotExist:
                return Response(
                    {'detail': 'no consent recorded'},
                    status=status.HTTP_204_NO_CONTENT,
                )
            return Response(EmployeeDataConsentSerializer(consent).data)

        # POST / DELETE require self-action (managers cannot sign on behalf
        # of an employee — that is the whole point of PDPA consent).
        if not is_self:
            return Response(
                {'error': 'data-consent must be created/revoked by the employee themselves'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.method == 'POST':
            consent_version = request.data.get('consent_version', '1.0')
            notes = request.data.get('notes', '')
            consent, created = EmployeeDataConsent.objects.update_or_create(
                employee=employee,
                defaults={
                    'consented_at': timezone.now(),
                    'revoked_at': None,
                    'consent_version': consent_version,
                    'notes': notes,
                },
            )
            return Response(
                EmployeeDataConsentSerializer(consent).data,
                status=(status.HTTP_201_CREATED if created else status.HTTP_200_OK),
            )

        # DELETE
        try:
            consent = employee.data_consent
        except EmployeeDataConsent.DoesNotExist:
            return Response(
                {'error': 'no consent to revoke'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if consent.revoked_at is None:
            consent.revoked_at = timezone.now()
            consent.save(update_fields=['revoked_at'])
        return Response(EmployeeDataConsentSerializer(consent).data)

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
