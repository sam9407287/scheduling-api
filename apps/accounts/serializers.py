"""
Account serializers
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Role

User = get_user_model()


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ['id', 'name', 'description', 'permissions']


class UserSerializer(serializers.ModelSerializer):
    role = RoleSerializer(read_only=True)
    role_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'firebase_uid', 'role', 'role_id', 'organization', 'branch',
            'phone', 'is_active', 'is_staff', 'date_joined', 'created_at'
        ]
        read_only_fields = ['id', 'firebase_uid', 'date_joined', 'created_at']


class UserProfileSerializer(serializers.ModelSerializer):
    """Simplified user profile for current user"""
    role_name = serializers.CharField(source='role.name', read_only=True, default=None)
    organization_name = serializers.CharField(source='organization.name', read_only=True, default=None)
    branch_name = serializers.CharField(source='branch.name', read_only=True, default=None)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'role_name', 'organization_name', 'branch_name',
            'phone', 'is_active'
        ]


class LoginSerializer(serializers.Serializer):
    """Login request (for local development/testing)"""
    username = serializers.CharField(help_text='使用者帳號')
    password = serializers.CharField(help_text='密碼', write_only=True)


class LoginResponseSerializer(serializers.Serializer):
    """Login response"""
    token = serializers.CharField(help_text='認證 Token')
    user = UserProfileSerializer()
