"""
Minimal LLM provider adapter for the free-tier scheduling endpoint.

Provider-agnostic on purpose: default is Google Gemini's free tier (best
tool/JSON reliability among free options); the env vars let Sam swap to any
OpenAI-compatible endpoint (Groq/DeepSeek/Ollama) without code changes.

  LLM_PROVIDER   gemini (default) | openai_compat
  LLM_API_KEY    API key (for gemini also accepts GEMINI_API_KEY)
  LLM_MODEL      default: gemini-3.6-flash
  LLM_FALLBACK_MODELS  gemini only: comma-separated models tried in order when
                 the primary is overloaded (429/503) or retired (404).
                 default: gemini-3.5-flash,gemini-3.5-flash-lite
  LLM_TOTAL_BUDGET_SECONDS  wall-clock cap across all retries/models (default 150)
  LLM_BASE_URL   openai_compat only, e.g. https://api.groq.com/openai/v1

Free-tier Gemini returns 503 "high demand" per *model*, not per key — while
one model is saturated its siblings usually answer, so the Gemini path retries
with exponential backoff and then walks the fallback list.
"""
import json
import logging
import os
import random
import time

import requests

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'gemini-3.6-flash'
DEFAULT_FALLBACK_MODELS = 'gemini-3.5-flash,gemini-3.5-flash-lite'

# Per-model retry policy for transient upstream errors. A free-tier 503 is not
# an instant rejection — Google queues the call and gives up after 15-35 s —
# so attempts are kept low and a wall-clock budget bounds the whole chain.
# 429 is deliberately NOT retried on the same model: on the free tier it means
# the per-model daily quota is gone, and the next model has its own quota.
TRANSIENT_STATUSES = (500, 502, 503, 504)
ATTEMPTS_PER_MODEL = 2
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 15.0
DEFAULT_TOTAL_BUDGET_SECONDS = 150.0


class LLMNotConfigured(Exception):
    pass


class LLMCallError(Exception):
    pass


def _config():
    provider = os.getenv('LLM_PROVIDER', 'gemini')
    api_key = os.getenv('LLM_API_KEY') or os.getenv('GEMINI_API_KEY')
    model = os.getenv('LLM_MODEL', DEFAULT_MODEL)
    base_url = os.getenv('LLM_BASE_URL', '')
    if not api_key:
        raise LLMNotConfigured(
            'LLM API key missing: set LLM_API_KEY (or GEMINI_API_KEY) in the environment.'
        )
    return provider, api_key, model, base_url


def generate_json(system_prompt: str, user_prompt: str, timeout: int = 90):
    """Call the configured model and parse its output as JSON.

    Returns (parsed_json, model_name). Raises LLMNotConfigured / LLMCallError.
    """
    provider, api_key, model, base_url = _config()

    if provider == 'gemini':
        raw, model = _call_gemini_with_fallback(
            api_key, _gemini_model_chain(model), system_prompt, user_prompt, timeout)
    else:
        raw = _call_openai_compat(api_key, model, base_url, system_prompt, user_prompt, timeout)

    try:
        return json.loads(_strip_fences(raw)), model
    except json.JSONDecodeError as exc:
        raise LLMCallError(f'model returned non-JSON output: {exc}') from exc


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text
        if text.endswith('```'):
            text = text[: -3]
    return text.strip()


def _gemini_model_chain(primary: str):
    """Primary model first, then LLM_FALLBACK_MODELS in order (deduplicated)."""
    raw = os.getenv('LLM_FALLBACK_MODELS', DEFAULT_FALLBACK_MODELS)
    chain = [primary]
    for name in raw.split(','):
        name = name.strip()
        if name and name not in chain:
            chain.append(name)
    return chain


def _backoff_seconds(attempt: int, response) -> float:
    """Exponential backoff with jitter; honour Retry-After when Gemini sends one."""
    retry_after = None
    if response is not None:
        try:
            retry_after = float(response.headers.get('Retry-After', ''))
        except (TypeError, ValueError):
            retry_after = None
    if retry_after:
        return min(retry_after, BACKOFF_MAX_SECONDS)
    base = BACKOFF_BASE_SECONDS * (2 ** attempt)
    return min(base + random.uniform(0, 1), BACKOFF_MAX_SECONDS)


def _call_gemini_with_fallback(api_key, models, system_prompt, user_prompt, timeout):
    """Try each model in turn; return (raw_text, model_name_that_answered).

    Per model: up to ATTEMPTS_PER_MODEL tries on transient statuses / network
    errors with backoff. 404 (model retired) and 4xx client errors other than
    429 skip straight to the next model — retrying them is pointless.
    """
    budget = float(os.getenv('LLM_TOTAL_BUDGET_SECONDS', DEFAULT_TOTAL_BUDGET_SECONDS))
    deadline = time.monotonic() + budget
    failures = []
    for model in models:
        response = None
        last_error = None
        for attempt in range(ATTEMPTS_PER_MODEL):
            remaining = deadline - time.monotonic()
            if remaining <= 5:
                failures.append(f'{model} -> skipped, {budget:.0f}s budget exhausted')
                raise LLMCallError(
                    'gemini unavailable after trying ' + '; '.join(failures)
                )
            try:
                response = _post_gemini(api_key, model, system_prompt, user_prompt,
                                        min(timeout, remaining))
            except requests.RequestException as exc:
                response = None
                last_error = f'network error: {exc.__class__.__name__}'
                logger.warning('gemini %s attempt %d: %s', model, attempt + 1, last_error)
            else:
                if response.status_code == 200:
                    if attempt or failures:
                        logger.info('gemini answered with %s after fallback/retry', model)
                    return _extract_gemini_text(response.json()), model
                last_error = f'HTTP {response.status_code}: {response.text[:200]}'
                logger.warning('gemini %s attempt %d: %s', model, attempt + 1,
                               last_error.splitlines()[0][:120])
                if response.status_code not in TRANSIENT_STATUSES:
                    break  # 429 quota / 404 retired / 400 / 403 — next model now
            if attempt < ATTEMPTS_PER_MODEL - 1:
                time.sleep(min(_backoff_seconds(attempt, response),
                               max(deadline - time.monotonic(), 0)))
        failures.append(f'{model} -> {last_error}')
    raise LLMCallError(
        'gemini unavailable after trying ' + '; '.join(failures)
    )


def _post_gemini(api_key, model, system_prompt, user_prompt, timeout):
    url = (
        'https://generativelanguage.googleapis.com/v1beta/models/'
        f'{model}:generateContent'
    )
    body = {
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': [{'role': 'user', 'parts': [{'text': user_prompt}]}],
        'generationConfig': {
            'response_mime_type': 'application/json',
            'temperature': 0.2,
        },
    }
    return requests.post(
        url, json=body, timeout=timeout,
        headers={'x-goog-api-key': api_key},
    )


def _extract_gemini_text(data) -> str:
    try:
        parts = data['candidates'][0]['content']['parts']
        texts = [p['text'] for p in parts if 'text' in p and not p.get('thought')]
        if not texts:
            raise KeyError('text')
        return ''.join(texts)
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMCallError(f'unexpected gemini response shape: {str(data)[:300]}') from exc


def _call_openai_compat(api_key, model, base_url, system_prompt, user_prompt, timeout):
    if not base_url:
        raise LLMNotConfigured('LLM_BASE_URL is required for openai_compat provider')
    response = requests.post(
        f'{base_url.rstrip("/")}/chat/completions',
        json={
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.2,
            'response_format': {'type': 'json_object'},
        },
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise LLMCallError(f'LLM HTTP {response.status_code}: {response.text[:300]}')
    return response.json()['choices'][0]['message']['content']
