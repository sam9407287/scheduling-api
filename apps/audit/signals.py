"""
Audit Log Signals
"""
import logging
import threading
import sys
from django.db import connection

logger = logging.getLogger(__name__)
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.contrib.contenttypes.models import ContentType

# Thread-local storage (single instance, not recreated each call)
_thread_locals = threading.local()

# Global flag to disable audit logging
_audit_disabled = False


def disable_audit():
    """Disable audit logging (for testing/migrations)"""
    global _audit_disabled
    _audit_disabled = True


def enable_audit():
    """Enable audit logging"""
    global _audit_disabled
    _audit_disabled = False


def get_request():
    """Get current request from thread local"""
    return getattr(_thread_locals, 'request', None)


def set_request(request):
    """Set current request in thread local"""
    _thread_locals.request = request


def _should_skip_audit(sender):
    """Check if audit logging should be skipped"""
    if _audit_disabled:
        return True

    # Skip audit's own models and Django internal models
    skip_labels = {'audit', 'sessions', 'contenttypes', 'admin', 'auth', 'migrations'}
    if sender._meta.app_label in skip_labels:
        return True

    # Check if audit table exists
    try:
        table_names = connection.introspection.table_names()
        if 'audit_auditlog' not in table_names:
            return True
    except Exception:
        return True

    return False


@receiver(pre_save)
def audit_pre_save(sender, instance, **kwargs):
    """Store old data before save"""
    if _should_skip_audit(sender):
        return

    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            instance._audit_old_data = serialize_model(old_instance)
        except sender.DoesNotExist:
            instance._audit_old_data = None
    else:
        instance._audit_old_data = None


@receiver(post_save)
def audit_post_save(sender, instance, created, **kwargs):
    """Log create/update operations"""
    if _should_skip_audit(sender):
        return

    request = get_request()
    user = None
    ip_address = None
    user_agent = ''

    if request:
        audit_info = getattr(request, '_audit_info', {})
        user = audit_info.get('user')
        ip_address = audit_info.get('ip_address')
        user_agent = audit_info.get('user_agent', '')

    action = 'create' if created else 'update'

    old_data = getattr(instance, '_audit_old_data', None)
    new_data = serialize_model(instance)

    # Calculate changes
    changes = {}
    if old_data:
        for key, value in new_data.items():
            if key not in old_data or old_data[key] != value:
                changes[key] = {
                    'old': old_data.get(key),
                    'new': value
                }

    try:
        from .models import AuditLog
        AuditLog.objects.create(
            user=user,
            action=action,
            model_name=f"{sender._meta.app_label}.{sender._meta.model_name}",
            record_id=instance.pk or 0,
            content_type=ContentType.objects.get_for_model(sender),
            object_id=instance.pk,
            old_data=old_data if not created else None,
            new_data=new_data if created else None,
            changes=changes if changes else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception as e:
        # Don't let audit logging failures break the application
        logger.error('Audit post_save failed for %s pk=%s: %s', sender.__name__, instance.pk, e, exc_info=True)


@receiver(post_delete)
def audit_post_delete(sender, instance, **kwargs):
    """Log delete operations"""
    if _should_skip_audit(sender):
        return

    request = get_request()
    user = None
    ip_address = None
    user_agent = ''

    if request:
        audit_info = getattr(request, '_audit_info', {})
        user = audit_info.get('user')
        ip_address = audit_info.get('ip_address')
        user_agent = audit_info.get('user_agent', '')

    old_data = serialize_model(instance)

    try:
        from .models import AuditLog
        AuditLog.objects.create(
            user=user,
            action='delete',
            model_name=f"{sender._meta.app_label}.{sender._meta.model_name}",
            record_id=instance.pk or 0,
            content_type=ContentType.objects.get_for_model(sender),
            object_id=instance.pk,
            old_data=old_data,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception as e:
        logger.error('Audit post_delete failed for %s pk=%s: %s', sender.__name__, instance.pk, e, exc_info=True)


def serialize_model(instance):
    """Serialize model instance to dict"""
    data = {}
    for field in instance._meta.fields:
        try:
            value = getattr(instance, field.name, None)

            # Handle special field types
            if value is None:
                data[field.name] = None
            elif hasattr(value, 'isoformat'):  # datetime, date, time
                data[field.name] = value.isoformat()
            elif hasattr(value, 'pk'):  # ForeignKey
                data[field.name] = value.pk
            else:
                try:
                    # Ensure JSON serializable
                    import json
                    json.dumps(value)
                    data[field.name] = value
                except (TypeError, ValueError):
                    data[field.name] = str(value)
        except Exception:
            data[field.name] = None

    return data
