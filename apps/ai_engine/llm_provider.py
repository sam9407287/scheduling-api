"""
Minimal LLM provider adapter for the free-tier scheduling endpoint.

Provider-agnostic on purpose: default is Google Gemini's free tier (best
tool/JSON reliability among free options); the env vars let Sam swap to any
OpenAI-compatible endpoint (Groq/DeepSeek/Ollama) without code changes.

  LLM_PROVIDER   gemini (default) | openai_compat
  LLM_API_KEY    API key (for gemini also accepts GEMINI_API_KEY)
  LLM_MODEL      default: gemini-2.0-flash
  LLM_BASE_URL   openai_compat only, e.g. https://api.groq.com/openai/v1
"""
import json
import os

import requests


class LLMNotConfigured(Exception):
    pass


class LLMCallError(Exception):
    pass


def _config():
    provider = os.getenv('LLM_PROVIDER', 'gemini')
    api_key = os.getenv('LLM_API_KEY') or os.getenv('GEMINI_API_KEY')
    model = os.getenv('LLM_MODEL', 'gemini-2.0-flash')
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
        raw = _call_gemini(api_key, model, system_prompt, user_prompt, timeout)
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


def _call_gemini(api_key, model, system_prompt, user_prompt, timeout):
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
    response = requests.post(
        url, json=body, timeout=timeout,
        headers={'x-goog-api-key': api_key},
    )
    if response.status_code != 200:
        raise LLMCallError(f'gemini HTTP {response.status_code}: {response.text[:300]}')
    data = response.json()
    try:
        return data['candidates'][0]['content']['parts'][0]['text']
    except (KeyError, IndexError) as exc:
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
