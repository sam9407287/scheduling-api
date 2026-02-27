"""
Firebase Authentication for Django REST Framework
"""
import os
import json
import firebase_admin
from firebase_admin import credentials, auth
from rest_framework import authentication, exceptions
from django.contrib.auth import get_user_model

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
    """
    Firebase JWT Token Authentication
    """
    
    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        
        if not auth_header:
            return None
        
        try:
            # Extract token from "Bearer <token>"
            token = auth_header.split(' ')[1]
        except IndexError:
            raise exceptions.AuthenticationFailed('Invalid token format. Use "Bearer <token>"')
        
        try:
            # Verify Firebase token
            get_firebase_app()
            decoded_token = auth.verify_id_token(token)
            firebase_uid = decoded_token['uid']
        except Exception as e:
            raise exceptions.AuthenticationFailed(f'Invalid token: {str(e)}')
        
        # Get or create user
        try:
            user = User.objects.get(firebase_uid=firebase_uid)
        except User.DoesNotExist:
            # Create user if doesn't exist
            email = decoded_token.get('email', '')
            name = decoded_token.get('name', '')
            
            user = User.objects.create_user(
                username=email or firebase_uid,
                email=email,
                firebase_uid=firebase_uid,
                first_name=name.split()[0] if name else '',
                last_name=' '.join(name.split()[1:]) if len(name.split()) > 1 else '',
            )
        
        return (user, None)
