from django.apps import AppConfig
from django.conf import settings


class AuditConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.audit'

    def ready(self):
        import apps.audit.signals  # noqa

        # Disable audit signals during testing
        if getattr(settings, 'AUDIT_DISABLED', False):
            from apps.audit.signals import disable_audit
            disable_audit()
