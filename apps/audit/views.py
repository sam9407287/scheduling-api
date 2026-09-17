"""
Audit views — read-only. AuditLog rows are written exclusively by the
signal/middleware pipeline (and a few explicit writes like unapprove);
the API never mutates them, preserving the tamper-evident guarantee.
"""
from rest_framework import viewsets

from apps.accounts.permissions import IsManager
from .models import AuditLog
from .serializers import AuditLogSerializer, AuditLogDetailSerializer


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """操作日誌查詢（manager+）。

    Org isolation：AuditLog 本身無 organization 欄位，以「操作者所屬機構」
    界定可見範圍——非 superuser 只看得到自己機構使用者的操作；user=None 的
    系統寫入（Celery 等）僅 superuser 可見。
    """
    queryset = AuditLog.objects.select_related('user')
    permission_classes = [IsManager]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return AuditLogDetailSerializer
        return AuditLogSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if not user.is_superuser:
            if user.organization:
                queryset = queryset.filter(user__organization=user.organization)
            else:
                return queryset.none()

        params = self.request.query_params
        if params.get('action'):
            queryset = queryset.filter(action=params['action'])
        if params.get('model_name'):
            queryset = queryset.filter(model_name__iexact=params['model_name'])
        if params.get('user'):
            queryset = queryset.filter(user_id=params['user'])
        if params.get('date_from'):
            queryset = queryset.filter(timestamp__date__gte=params['date_from'])
        if params.get('date_to'):
            queryset = queryset.filter(timestamp__date__lte=params['date_to'])
        search = params.get('search')
        if search:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(model_name__icontains=search)
                | Q(user__username__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )
        return queryset
