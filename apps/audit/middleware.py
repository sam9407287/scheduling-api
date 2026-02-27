"""
Audit Log Middleware
"""
import json
from django.utils import timezone
from .models import AuditLog


class AuditLogMiddleware:
    """
    Middleware to log all model changes
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Set request in thread local for signals
        from .signals import set_request
        set_request(request)
        
        response = self.get_response(request)
        return response
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        """Process view before execution"""
        # Store request info for audit logging
        request._audit_info = {
            'user': request.user if hasattr(request, 'user') and request.user.is_authenticated else None,
            'ip_address': self.get_client_ip(request),
            'user_agent': request.META.get('HTTP_USER_AGENT', ''),
        }
        return None
    
    @staticmethod
    def get_client_ip(request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
