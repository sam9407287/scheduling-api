"""
Gemini transport resilience: retry with backoff on 429/503, then walk the
fallback model chain; 404 (retired model) skips straight to the next model.
Free-tier "high demand" 503s are per-model, so this is what turns the
AI 排班 button from flaky into reliable.
"""
import json

import pytest
import requests

from apps.ai_engine import llm_provider


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def _ok(text='{"assignments": []}'):
    return FakeResponse(200, {'candidates': [{'content': {'parts': [{'text': text}]}}]})


def _busy():
    return FakeResponse(503, {'error': {'code': 503, 'status': 'UNAVAILABLE',
                                        'message': 'high demand'}})


@pytest.fixture
def transport(monkeypatch):
    """Queue of responses (or exceptions) keyed by call order; records models hit."""
    calls = []
    queue = []

    def fake_post(url, json=None, timeout=None, headers=None):
        model = url.rsplit('/models/', 1)[1].split(':')[0]
        calls.append(model)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(llm_provider.requests, 'post', fake_post)
    monkeypatch.setattr(llm_provider.time, 'sleep', lambda s: None)
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.delenv('LLM_PROVIDER', raising=False)
    monkeypatch.setenv('LLM_MODEL', 'primary')
    monkeypatch.setenv('LLM_FALLBACK_MODELS', 'backup-a, backup-b')
    return calls, queue


class TestModelChain:
    def test_chain_dedupes_and_orders(self, monkeypatch):
        monkeypatch.setenv('LLM_FALLBACK_MODELS', 'x, primary ,y,,x')
        assert llm_provider._gemini_model_chain('primary') == ['primary', 'x', 'y']

    def test_default_chain_starts_with_default_model(self, monkeypatch):
        monkeypatch.delenv('LLM_FALLBACK_MODELS', raising=False)
        chain = llm_provider._gemini_model_chain(llm_provider.DEFAULT_MODEL)
        assert chain[0] == llm_provider.DEFAULT_MODEL
        assert len(chain) >= 2


class TestRetryAndFallback:
    def test_first_try_success_hits_only_primary(self, transport):
        calls, queue = transport
        queue.append(_ok())
        parsed, model = llm_provider.generate_json('sys', 'user')
        assert parsed == {'assignments': []}
        assert model == 'primary'
        assert calls == ['primary']

    def test_503_retries_same_model_then_succeeds(self, transport):
        calls, queue = transport
        n = llm_provider.ATTEMPTS_PER_MODEL
        queue.extend([_busy()] * (n - 1) + [_ok()])
        _, model = llm_provider.generate_json('sys', 'user')
        assert model == 'primary'
        assert calls == ['primary'] * n

    def test_exhausted_primary_falls_back_and_reports_real_model(self, transport):
        calls, queue = transport
        queue.extend([_busy()] * llm_provider.ATTEMPTS_PER_MODEL + [_ok()])
        _, model = llm_provider.generate_json('sys', 'user')
        assert model == 'backup-a'
        assert calls == ['primary'] * llm_provider.ATTEMPTS_PER_MODEL + ['backup-a']

    def test_429_quota_skips_to_next_model_without_retry(self, transport):
        calls, queue = transport
        queue.extend([FakeResponse(429, {'error': {'status': 'RESOURCE_EXHAUSTED'}}), _ok()])
        _, model = llm_provider.generate_json('sys', 'user')
        assert model == 'backup-a'
        assert calls == ['primary', 'backup-a']

    def test_total_budget_stops_chain(self, transport, monkeypatch):
        calls, queue = transport
        monkeypatch.setenv('LLM_TOTAL_BUDGET_SECONDS', '10')
        clock = [0.0]
        monkeypatch.setattr(llm_provider.time, 'monotonic', lambda: clock[0])

        def slow_busy(*a, **kw):
            clock[0] += 4.0  # each 503 takes 4 s of wall clock
            calls.append('tick')
            return _busy()
        monkeypatch.setattr(llm_provider, '_post_gemini', slow_busy)
        with pytest.raises(llm_provider.LLMCallError) as exc:
            llm_provider.generate_json('sys', 'user')
        assert 'budget exhausted' in str(exc.value)
        assert calls.count('tick') <= 3  # never all 6 calls of the full chain

    def test_404_retired_model_skips_without_retry(self, transport):
        calls, queue = transport
        queue.extend([FakeResponse(404, {'error': 'not found'}), _ok()])
        _, model = llm_provider.generate_json('sys', 'user')
        assert model == 'backup-a'
        assert calls == ['primary', 'backup-a']

    def test_network_error_counts_as_transient(self, transport):
        calls, queue = transport
        queue.extend([requests.ConnectionError('boom'), _ok()])
        _, model = llm_provider.generate_json('sys', 'user')
        assert model == 'primary'
        assert calls == ['primary', 'primary']

    def test_all_models_busy_raises_with_summary(self, transport):
        calls, queue = transport
        queue.extend([_busy()] * (llm_provider.ATTEMPTS_PER_MODEL * 3))
        with pytest.raises(llm_provider.LLMCallError) as exc:
            llm_provider.generate_json('sys', 'user')
        msg = str(exc.value)
        for name in ('primary', 'backup-a', 'backup-b'):
            assert name in msg
        assert 'HTTP 503' in msg
        assert len(calls) == llm_provider.ATTEMPTS_PER_MODEL * 3

    def test_thought_parts_are_ignored(self, transport):
        calls, queue = transport
        queue.append(FakeResponse(200, {'candidates': [{'content': {'parts': [
            {'text': 'thinking...', 'thought': True},
            {'text': '{"assignments": [1]}'},
        ]}}]}))
        parsed, _ = llm_provider.generate_json('sys', 'user')
        assert parsed == {'assignments': [1]}


class TestBackoff:
    def test_honours_retry_after_capped(self):
        assert llm_provider._backoff_seconds(0, FakeResponse(503, headers={'Retry-After': '4'})) == 4
        assert llm_provider._backoff_seconds(
            0, FakeResponse(503, headers={'Retry-After': '999'})) == llm_provider.BACKOFF_MAX_SECONDS

    def test_exponential_with_jitter_within_bounds(self):
        for attempt in range(4):
            value = llm_provider._backoff_seconds(attempt, None)
            base = llm_provider.BACKOFF_BASE_SECONDS * (2 ** attempt)
            assert min(base, llm_provider.BACKOFF_MAX_SECONDS) <= value <= min(
                base + 1, llm_provider.BACKOFF_MAX_SECONDS)
