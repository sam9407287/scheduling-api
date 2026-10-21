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
    # 登入身分 → Employee 的唯一可信對應；無 Employee profile 時為 null，
    # 前端不得用 username/姓名去員工列表猜測（LEAVE_V2 P0）。
    employee_pk = serializers.SerializerMethodField()
    employee_code = serializers.SerializerMethodField()
    firebase_linked = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'role_name', 'organization', 'organization_name',
            'branch', 'branch_name', 'employee_pk', 'employee_code',
            'firebase_linked', 'phone', 'is_active'
        ]

    def get_employee_pk(self, obj):
        profile = getattr(obj, 'employee_profile', None)
        return profile.pk if profile else None

    def get_employee_code(self, obj):
        profile = getattr(obj, 'employee_profile', None)
        return profile.employee_id if profile else None

    def get_firebase_linked(self, obj):
        return bool(obj.firebase_uid)


class LoginSerializer(serializers.Serializer):
    """Login request（帳號密碼登入；username 或 email 擇一）"""
    username = serializers.CharField(help_text='使用者帳號（或直接填 email）', required=False)
    email = serializers.EmailField(help_text='Email（自助註冊的帳號用這個登入）', required=False)
    password = serializers.CharField(help_text='密碼', write_only=True)

    def validate(self, data):
        if not data.get('username') and not data.get('email'):
            raise serializers.ValidationError({'username': 'username 或 email 必填一個'})
        return data


class RegisterSendCodeSerializer(serializers.Serializer):
    """POST /auth/register/send-code/"""
    email = serializers.EmailField(help_text='要註冊的信箱（任何信箱都可以，不限 Gmail）')


class RegisterSerializer(serializers.Serializer):
    """POST /auth/register/"""
    email = serializers.EmailField()
    code = serializers.CharField(min_length=6, max_length=6, help_text='信中的 6 位數驗證碼')
    password = serializers.CharField(write_only=True, min_length=8, max_length=128)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=20)


class RegisterResponseSerializer(serializers.Serializer):
    token = serializers.CharField()
    user = UserProfileSerializer()


class LoginResponseSerializer(serializers.Serializer):
    """Login response"""
    token = serializers.CharField(help_text='認證 Token')
    user = UserProfileSerializer()
