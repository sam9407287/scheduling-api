"""
Account views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema
from django.contrib.auth import get_user_model, authenticate
from .models import Role
from .serializers import (
    UserSerializer, UserProfileSerializer, RoleSerializer,
    LoginSerializer, LoginResponseSerializer,
    RegisterSendCodeSerializer, RegisterSerializer, RegisterResponseSerializer,
)
from .permissions import IsAdmin, IsManager

User = get_user_model()


def _client_ip(request) -> str | None:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip() or None
    return request.META.get('REMOTE_ADDR') or None


class LoginView(APIView):
    """Session/Token login for development & testing"""
    permission_classes = [AllowAny]

    @extend_schema(
        request=LoginSerializer,
        responses={200: LoginResponseSerializer},
        summary='登入取得 Token',
        description='使用帳號密碼登入，取得認證 Token（僅用於本地開發/測試）',
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        username = serializer.validated_data.get('username')
        password = serializer.validated_data['password']

        # 自助註冊的帳號 username == email；舊帳號也允許用 email 登入
        login_id = (username or serializer.validated_data.get('email') or '').strip()
        if '@' in login_id and not User.objects.filter(username=login_id).exists():
            by_email = list(User.objects.filter(email__iexact=login_id)[:2])
            if len(by_email) == 1:
                login_id = by_email[0].username

        user = authenticate(request, username=login_id, password=password)
        if user is None:
            return Response(
                {'error': '帳號或密碼錯誤'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Try Token auth first
        try:
            from rest_framework.authtoken.models import Token
            token, _ = Token.objects.get_or_create(user=user)
            return Response({
                'token': token.key,
                'user': UserProfileSerializer(user).data,
            })
        except Exception:
            # Fallback: session login
            from django.contrib.auth import login
            login(request, user)
            return Response({
                'message': '登入成功 (session)',
                'user': UserProfileSerializer(user).data,
            })


class RegisterSendCodeView(APIView):
    """自助註冊第一步：寄驗證碼到信箱（任何信箱，不限 Gmail）。"""
    permission_classes = [AllowAny]
    authentication_classes = []  # 未登入也能打；避免帶壞 token 被 401

    @extend_schema(
        request=RegisterSendCodeSerializer,
        responses={200: None},
        summary='寄送註冊驗證碼',
        description=(
            '寄 6 位數驗證碼到指定信箱。429 = 太頻繁（同 email 60 秒一次、'
            '每小時 5 次；同 IP 每小時 20 次），409 = 該 email 已有帳號。'
        ),
    )
    def post(self, request):
        from django.conf import settings as dj_settings
        from . import registration

        serializer = RegisterSendCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = registration.normalize_email(serializer.validated_data['email'])

        if User.objects.filter(email__iexact=email).exists():
            return Response(
                {'code': 'email_already_registered',
                 'error': '此 email 已經註冊過，請直接登入（或用 Google 登入）'},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            issued = registration.issue_code(email, _client_ip(request))
        except registration.RateLimited as exc:
            return Response(
                {'code': 'too_many_requests', 'reason': exc.reason,
                 'retry_after_seconds': exc.retry_after_seconds,
                 'error': '請求太頻繁，請稍後再試'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={'Retry-After': str(exc.retry_after_seconds)},
            )
        try:
            registration.send_code_email(email, issued.code, issued.ttl_seconds)
        except Exception as exc:  # SMTP down / bad credentials
            issued.row.delete()  # 沒寄出去的碼不佔 cooldown
            return Response(
                {'code': 'email_send_failed',
                 'error': f'驗證信寄送失敗，請稍後再試（{exc.__class__.__name__}）'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        payload = {
            'message': '驗證碼已寄出，請到信箱查看',
            'email': email,
            'expires_in_seconds': issued.ttl_seconds,
            'cooldown_seconds': issued.cooldown_seconds,
        }
        # 本地開發沒有真的寄信（console backend）：直接回驗證碼方便前端串接。
        if getattr(dj_settings, 'EMAIL_VERIFICATION_EXPOSE_CODE', False):
            payload['debug_code'] = issued.code
        return Response(payload)


class RegisterView(APIView):
    """自助註冊第二步：驗證碼 + 密碼 → 建帳號並回傳 Token。

    新帳號 = manager、organization=None（跟 Google 登入一樣，進系統後自建機構）。
    一個 email 只能有一個帳號；username 就是 email。
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        request=RegisterSerializer,
        responses={201: RegisterResponseSerializer},
        summary='完成註冊（驗證碼 + 密碼）',
        description=(
            '400 code=invalid_code / code_expired / code_locked / weak_password；'
            '409 code=email_already_registered。成功回 201 + Token。'
        ),
    )
    def post(self, request):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.db import IntegrityError, transaction
        from django.utils import timezone
        from rest_framework.authtoken.models import Token
        from . import registration

        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = registration.normalize_email(data['email'])

        if User.objects.filter(email__iexact=email).exists():
            return Response(
                {'code': 'email_already_registered', 'error': '此 email 已經註冊過'},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            code_row = registration.verify_code(email, data['code'])
        except registration.CodeInvalid as exc:
            messages = {
                'invalid_code': '驗證碼錯誤',
                'code_expired': '驗證碼已過期或尚未索取，請重新寄送',
                'code_locked': '驗證失敗次數過多，請重新寄送驗證碼',
            }
            return Response({'code': exc.code, 'error': messages[exc.code]},
                            status=status.HTTP_400_BAD_REQUEST)

        probe = User(username=email, email=email,
                     first_name=data.get('first_name', ''), last_name=data.get('last_name', ''))
        try:
            validate_password(data['password'], user=probe)
        except DjangoValidationError as exc:
            return Response({'code': 'weak_password', 'error': '密碼強度不足',
                             'details': list(exc.messages)},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                manager_role, _ = Role.objects.get_or_create(
                    name='manager', defaults={'description': '管理者', 'permissions': {}},
                )
                user = User.objects.create_user(
                    username=email, email=email, password=data['password'],
                    first_name=data.get('first_name', ''),
                    last_name=data.get('last_name', ''),
                    phone=data.get('phone', ''),
                    role=manager_role, organization=None,
                )
                code_row.consumed_at = timezone.now()
                code_row.save(update_fields=['consumed_at'])
                token, _ = Token.objects.get_or_create(user=user)
        except IntegrityError:
            return Response(
                {'code': 'email_already_registered', 'error': '此 email 已經註冊過'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {'token': token.key, 'user': UserProfileSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    """Role API (read-only)"""
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated]


class UserViewSet(viewsets.ModelViewSet):
    """User management API"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsManager]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by organization if user is not admin
        if not self.request.user.is_superuser:
            if self.request.user.organization:
                queryset = queryset.filter(organization=self.request.user.organization)
        
        return queryset
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def me(self, request):
        """Get current user profile"""
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)
    
    @action(detail=False, methods=['patch'], permission_classes=[IsAuthenticated])
    def update_profile(self, request):
        """Update current user profile"""
        serializer = UserProfileSerializer(
            request.user,
            data=request.data,
            partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
