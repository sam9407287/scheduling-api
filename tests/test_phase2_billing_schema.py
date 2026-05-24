"""
Phase 2 PR7 — metered billing schema, pure helpers, and read-only API.

Covers the model invariants the AI generate hot path (PR8) is going to
depend on:

  * The seeded rates from migration 0002 produce known token costs via
    `estimate_tokens()`.
  * `record_usage` updates the BillingPeriod total atomically and creates
    a UsageRecord even on `solver_status='infeasible'` (pre-debit rule).
  * Concurrent `record_usage` calls on the same org accumulate without
    losing writes (lock-correctness sanity).
  * `would_exceed_cap` returns the right combination of current /
    projected / cap.

And the customer-facing surface:

  * GET /api/billing/rates/                — returns at least the three seeded rates
  * GET /api/billing/usage/                — returns period + cap stats
  * GET / PATCH /api/billing/settings/     — set monthly cap
  * POST /api/billing/estimate/            — dry-run cost preview, no DB write
"""
import threading
import pytest  # noqa: F401  (conftest fixtures)
from datetime import datetime, timezone as dt_tz

from django.db import connection

from apps.billing.models import (
    BillingRateConfig, OrgBillingSettings, BillingPeriod, UsageRecord,
    estimate_tokens, record_usage, would_exceed_cap,
)


# ===========================================================================
# Pure helpers (no HTTP)
# ===========================================================================

class TestEstimateTokens:
    def test_seeded_rates_resolve(self, db):
        # Seeded in migration 0002.
        assert estimate_tokens('generate') == 10
        assert estimate_tokens('fill_gaps') == 5
        assert estimate_tokens('derive_legal') == 3

    def test_unknown_mode_returns_zero(self, db):
        assert estimate_tokens('not_a_mode') == 0

    def test_admin_can_supersede_with_a_new_effective_row(self, db):
        # Insert a fresh row with a later effective_from for 'generate'.
        # The pure helper must pick it up.
        BillingRateConfig.objects.create(
            billing_mode='generate', tokens_per_call=42,
            notes='price hike',
        )
        assert estimate_tokens('generate') == 42


class TestRecordUsage:
    def test_pre_debit_records_even_when_infeasible(self, db, organization):
        before = UsageRecord.objects.count()
        record_usage(
            organization=organization,
            billing_mode='generate',
            solver_status='infeasible',
        )
        assert UsageRecord.objects.count() == before + 1
        rec = UsageRecord.objects.latest('created_at')
        assert rec.solver_status == 'infeasible'
        assert rec.tokens_charged == 10  # the customer pays even for INFEASIBLE

    def test_billing_period_total_increments(self, db, organization):
        record_usage(organization, 'generate', 'success')
        record_usage(organization, 'fill_gaps', 'success')
        period = BillingPeriod.current_for(organization)
        period.refresh_from_db()
        assert period.total_tokens == 15  # 10 + 5

    def test_serialised_writes_accumulate_correctly(self, db, organization):
        """Five sequential record_usage calls land five rows, total = 15."""
        for _ in range(5):
            record_usage(organization, 'derive_legal', 'success')
        period = BillingPeriod.current_for(organization)
        period.refresh_from_db()
        assert period.total_tokens == 15  # 5 × 3
        assert UsageRecord.objects.filter(organization=organization).count() == 5

    @pytest.mark.skipif(
        connection.vendor == 'sqlite',
        reason='SQLite in-memory does not support cross-thread row locks; '
               'this concurrency assertion runs only when CI uses Postgres.',
    )
    def test_concurrent_writes_do_not_lose_tokens(self, db, organization):
        """
        Lock-correctness check on `select_for_update` inside record_usage.
        Skipped under SQLite because its single-writer model raises
        "database is locked" rather than serialising thread access.
        """
        errors = []

        def worker():
            try:
                record_usage(organization, 'derive_legal', 'success')
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], errors

        period = BillingPeriod.current_for(organization)
        period.refresh_from_db()
        assert period.total_tokens == 15
        assert UsageRecord.objects.filter(organization=organization).count() == 5


class TestWouldExceedCap:
    def test_unlimited_org_never_exceeds(self, db, organization):
        # No OrgBillingSettings row → cap is None → never exceeds.
        exceeds, current, projected, cap = would_exceed_cap(
            organization, 'generate'
        )
        assert exceeds is False
        assert current == 0
        assert projected == 10
        assert cap is None

    def test_cap_short_of_request(self, db, organization):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=5,
        )
        exceeds, current, projected, cap = would_exceed_cap(
            organization, 'generate'  # 10 tokens
        )
        assert exceeds is True
        assert projected == 10
        assert cap == 5

    def test_cap_just_enough(self, db, organization):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=10,
        )
        exceeds, *_ = would_exceed_cap(organization, 'generate')
        assert exceeds is False


# ===========================================================================
# HTTP surface
# ===========================================================================

class TestRatesEndpoint:
    def test_list_seeded_rates(self, db, admin_api_client):
        resp = admin_api_client.get('/api/billing/rates/')
        assert resp.status_code == 200
        modes = {r['billing_mode'] for r in resp.json()['results']}
        assert {'generate', 'fill_gaps', 'derive_legal'}.issubset(modes)


class TestSettingsEndpoint:
    def test_get_creates_on_first_access(self, db, manager_api_client):
        resp = manager_api_client.get('/api/billing/settings/')
        assert resp.status_code == 200
        assert resp.json()['monthly_cap_tokens'] is None
        # Subsequent GET hits the same row.
        resp2 = manager_api_client.get('/api/billing/settings/')
        assert resp2.json()['id'] == resp.json()['id']

    def test_patch_sets_cap(self, db, manager_api_client):
        resp = manager_api_client.patch(
            '/api/billing/settings/',
            {'monthly_cap_tokens': 200, 'alert_threshold_pct': 75},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.json()['monthly_cap_tokens'] == 200
        assert resp.json()['alert_threshold_pct'] == 75

    def test_non_manager_blocked(self, db, employee_api_client):
        resp = employee_api_client.get('/api/billing/settings/')
        assert resp.status_code == 403


class TestUsageEndpoint:
    def test_empty_usage_returns_zero_total(self, db, manager_api_client):
        resp = manager_api_client.get('/api/billing/usage/')
        assert resp.status_code == 200
        body = resp.json()
        assert body['period']['total_tokens'] == 0
        assert body['records'] == []
        assert body['cap'] is None

    def test_after_record_usage_records_visible(
        self, db, manager_api_client, organization
    ):
        record_usage(organization, 'generate', 'success')
        record_usage(organization, 'derive_legal', 'infeasible')
        resp = manager_api_client.get('/api/billing/usage/')
        assert resp.status_code == 200
        body = resp.json()
        assert body['period']['total_tokens'] == 13  # 10 + 3
        assert len(body['records']) == 2

    def test_cap_pct_used_reported(
        self, db, manager_api_client, organization
    ):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=100,
        )
        record_usage(organization, 'generate', 'success')  # 10 tokens
        resp = manager_api_client.get('/api/billing/usage/')
        body = resp.json()
        assert body['cap'] == 100
        assert body['cap_pct_used'] == 10.0


class TestEstimateEndpoint:
    def test_estimate_returns_tokens_and_projection(
        self, db, manager_api_client, organization
    ):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=50,
        )
        record_usage(organization, 'generate', 'success')  # 10 used

        resp = manager_api_client.post(
            '/api/billing/estimate/',
            {'billing_mode': 'fill_gaps'},  # cost = 5
            format='json',
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body['tokens_to_charge'] == 5
        assert body['current_period_tokens'] == 10
        assert body['projected_period_tokens'] == 15
        assert body['monthly_cap_tokens'] == 50
        assert body['would_exceed_cap'] is False

    def test_estimate_flags_cap_overrun(
        self, db, manager_api_client, organization
    ):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=8,
        )
        resp = manager_api_client.post(
            '/api/billing/estimate/',
            {'billing_mode': 'generate'},  # 10 > 8
            format='json',
        )
        assert resp.status_code == 200
        assert resp.json()['would_exceed_cap'] is True

    def test_estimate_rejects_unknown_mode(self, db, manager_api_client):
        resp = manager_api_client.post(
            '/api/billing/estimate/',
            {'billing_mode': 'unknown'},
            format='json',
        )
        assert resp.status_code == 400

    def test_estimate_does_not_write(
        self, db, manager_api_client, organization
    ):
        before_records = UsageRecord.objects.count()
        before_period = BillingPeriod.current_for(organization).total_tokens
        manager_api_client.post(
            '/api/billing/estimate/',
            {'billing_mode': 'generate'}, format='json',
        )
        period = BillingPeriod.current_for(organization)
        period.refresh_from_db()
        assert UsageRecord.objects.count() == before_records
        assert period.total_tokens == before_period
