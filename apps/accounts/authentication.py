"""
Firebase Authentication for Django REST Framework
"""
import os
import json
import firebase_admin
from firebase_admin import credentials, auth
from rest_framework import authentication, exceptions
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from .models import Role

User = get_user_model()

# Initialize Firebase Admin SDK
_firebase_app = None


def get_firebase_app():
    """Initialize and return Firebase app"""
    global _firebase_app
    if _firebase_app is None:
        creds_path = os.getenv('FIREBASE_CREDENTIALS_PATH')
        creds_json = os.getenv('FIREBASE_CREDENTIALS_JSON')
        
        if creds_path and os.path.exists(creds_path):
            cred = credentials.Certificate(creds_path)
        elif creds_json:
            cred_info = json.loads(creds_json)
            cred = credentials.Certificate(cred_info)
        else:
            # For development, use default credentials if available
            try:
                cred = credentials.ApplicationDefault()
            except Exception:
                raise ValueError(
                    "Firebase credentials not found. "
                    "Set FIREBASE_CREDENTIALS_PATH or FIREBASE_CREDENTIALS_JSON"
                )
        
        _firebase_app = firebase_admin.initialize_app(cred)
    
    return _firebase_app


class FirebaseAuthentication(authentication.BaseAuthentication):
    """Firebase JWT authentication with MVP self-service provisioning.

    Product decision (2026-09-17, MVP): every Google login IS a manager.
    - Known firebase_uid → that user.
    - Verified email matching exactly one existing user → bind uid to it
      (keeps their role/organization — e.g. pre-created admin accounts).
    - Otherwise → auto-provision: a fresh Organization (their own isolated
      tenant) + a manager-role User. Org isolation does the rest: each
      Google account only ever sees its own organization's data.

    Error contract (per GOOGLE_LOGIN handoff §9):
      401 invalid_firebase_token / 403 email_not_verified / 403 account_inactive
    """

    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')

        if not auth_header:
            return None

        # Only handle the Bearer scheme. Other schemes (e.g. "Token <key>")
        # belong to other authentication classes — returning None lets DRF
        # fall through to them instead of failing the whole request here.
        parts = auth_header.split(' ')
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return None
        token = parts[1]

        try:
            get_firebase_app()
            decoded_token = auth.verify_id_token(token)
            firebase_uid = decoded_token['uid']
        except Exception as e:
            raise exceptions.AuthenticationFailed({
                'code': 'invalid_firebase_token',
                'message': f'Invalid token: {str(e)}',
            })

        user = User.objects.filter(firebase_uid=firebase_uid).first()
        if user is None:
            user = self._bind_or_provision(decoded_token, firebase_uid)

        if not user.is_active:
            raise exceptions.AuthenticationFailed({
                'code': 'account_inactive',
                'message': 'This account has been deactivated.',
            })

        return (user, None)

    def _bind_or_provision(self, decoded_token, firebase_uid):
        email = (decoded_token.get('email') or '').strip().lower()

        # 綁定/自動開通都以 email 為身分依據，必須是 Google 驗證過的 email
        if email and not decoded_token.get('email_verified', False):
            raise exceptions.AuthenticationFailed({
                'code': 'email_not_verified',
                'message': 'Google account email is not verified.',
            })

        # 預建帳號綁定：email 對到「恰好一個」尚未綁 Firebase 的帳號 → 回填 uid，
        # 保留原本的角色/機構（例如你預先建好的 admin/manager）。
        if email:
            matches = list(User.objects.filter(email__iexact=email)[:2])
            unbound = [u for u in matches if not u.firebase_uid]
            if len(matches) == 1 and unbound:
                user = unbound[0]
                user.firebase_uid = firebase_uid
                user.save(update_fields=['firebase_uid'])
                return user

        # MVP 自助開通：全新 Google 帳號 → 自己的機構 + manager 角色。
        # 機構隔離保證他只看得到自己這個空白租戶的資料。
        from django.db import transaction
        from apps.organizations.models import Organization

        name = decoded_token.get('name', '')
        name_parts = name.split() if name else []
        display = name or (email.split('@')[0] if email else firebase_uid[:8])

        try:
            with transaction.atomic():
                organization = Organization.objects.create(
                    name=f'{display} 的機構',
                    # 完整 uid 保證唯一（Firebase uid ≤ 36 字元，欄位上限 50）
                    code=f'G-{firebase_uid.upper()}'[:50],
                    email=email,
                )
                manager_role, _ = Role.objects.get_or_create(
                    name='manager', defaults={'description': '管理者', 'permissions': {}},
                )
                user = User.objects.create_user(
                    username=firebase_uid,
                    email=email,
                    firebase_uid=firebase_uid,
                    first_name=name_parts[0] if name_parts else '',
                    last_name=' '.join(name_parts[1:]) if len(name_parts) > 1 else '',
                    role=manager_role,
                    organization=organization,
                )
        except IntegrityError:
            # 並發：同一 firebase_uid 同時首登，取已建立者
            user = User.objects.filter(firebase_uid=firebase_uid).first()
            if user is None:
                raise exceptions.AuthenticationFailed({
                    'code': 'invalid_firebase_token',
                    'message': 'Unable to create or retrieve user.',
                })
        return user
