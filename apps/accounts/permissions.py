"""
Custom permissions for scheduling system
"""
from rest_framework import permissions


class IsAdmin(permissions.BasePermission):
    """Only admin users can access"""
    
    def has_permission(self, request, view):
        return (
            request.user and
            request.user.is_authenticated and
            (request.user.is_superuser or
             (request.user.role and request.user.role.name == 'admin'))
        )


class IsManager(permissions.BasePermission):
    """Manager or admin can access"""
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        if request.user.is_superuser:
            return True
        
        if request.user.role:
            return request.user.role.name in ['admin', 'manager']
        
        return False


class IsSupervisor(permissions.BasePermission):
    """Supervisor, manager or admin can access"""
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        if request.user.is_superuser:
            return True
        
        if request.user.role:
            return request.user.role.name in ['admin', 'manager', 'supervisor']
        
        return False


class IsEmployeeOrAbove(permissions.BasePermission):
    """Any authenticated employee can access"""
    
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated


class IsOwnerOrManager(permissions.BasePermission):
    """Owner of the resource or manager/admin can access"""
    
    def has_object_permission(self, request, view, obj):
        # Admin/Manager can access all
        if request.user.is_superuser:
            return True
        
        if request.user.role and request.user.role.name in ['admin', 'manager']:
            return True
        
        # Check if user owns the resource
        if hasattr(obj, 'user'):
            return obj.user == request.user
        elif hasattr(obj, 'employee') and hasattr(obj.employee, 'user'):
            return obj.employee.user == request.user
        
        return False
