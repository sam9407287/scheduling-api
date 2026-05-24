"""
Seed the three Phase 2 billing rates so the very first /api/billing/rates/
call returns something usable, and `estimate_tokens()` doesn't silently
return 0 for an unconfigured mode.

Numbers are placeholders — admin can edit via Django admin without a
migration. Customer confirmed flat-fee per mode in PR7 alignment chat
(2026-05-25).
"""
from django.db import migrations
from django.utils import timezone


DEFAULT_RATES = [
    ('generate', 10, 'Full AI generation of B (most expensive)'),
    ('fill_gaps', 5, 'AI partial fill of a partially-drafted B'),
    ('derive_legal', 3, 'B → A repair (cheapest because it is constrained)'),
]


def seed(apps, schema_editor):
    BillingRateConfig = apps.get_model('billing', 'BillingRateConfig')
    now = timezone.now()
    for mode, tokens, notes in DEFAULT_RATES:
        BillingRateConfig.objects.get_or_create(
            billing_mode=mode,
            defaults={
                'tokens_per_call': tokens,
                'effective_from': now,
                'notes': notes,
            },
        )


def unseed(apps, schema_editor):
    BillingRateConfig = apps.get_model('billing', 'BillingRateConfig')
    BillingRateConfig.objects.filter(
        billing_mode__in=[m for m, _, _ in DEFAULT_RATES]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('billing', '0001_initial'),
    ]
    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
