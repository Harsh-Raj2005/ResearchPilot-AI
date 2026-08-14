"""
Tests for app.services.llm_service.

No real OpenAI API call is ever made — every test monkeypatches
llm_service._get_client() to return a fake client whose
`.chat.completions.create()` is under full test control. No
OPENAI_API_KEY or network access is required to run this file.
"""
from types import SimpleNamespace

import openai
import pytest

from app.core.config import settings
from app.services import llm_service


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletionsResource:
    """Stands in for client.chat.completions — records calls, returns
    a pre-configured response or raises a pre-configured exception."""

    def __init__(self, *, content=None, error=None):
        self._content = content
        self._error = error
        self.calls: list[dict] = []

    async def create(self, *, model, messages):
        self.calls.append({"model": model, "messages": messages})
        if self._error is not None:
            raise self._error
        return SimpleNamespace(choices=[_FakeChoice(self._content)])


class _FakeClient:
    def __init__(self, *, content=None, error=None):
        self.chat = SimpleNamespace(
            completions=_FakeCompletionsResource(content=content, error=error)
        )


def _patch_client(monkeypatch, fake_client):
    monkeypatch.setattr(llm_service, "_get_client", lambda: fake_client)


# --- 1. Correct model is passed ---


async def test_generate_answer_uses_configured_model(monkeypatch):
    fake_client = _FakeClient(content="The answer.")
    _patch_client(monkeypatch, fake_client)

    await llm_service.generate_answer("system instructions", "user question")

    assert fake_client.chat.completions.calls[0]["model"] == settings.llm_model


# --- 2. Correct system/user messages are passed ---


async def test_generate_answer_sends_correct_messages(monkeypatch):
    fake_client = _FakeClient(content="The answer.")
    _patch_client(monkeypatch, fake_client)

    await llm_service.generate_answer("be helpful and grounded", "what is X?")

    messages = fake_client.chat.completions.calls[0]["messages"]
    assert messages == [
        {"role": "system", "content": "be helpful and grounded"},
        {"role": "user", "content": "what is X?"},
    ]


# --- 3. Generated text is returned ---


async def test_generate_answer_returns_generated_text(monkeypatch):
    fake_client = _FakeClient(content="This is the generated answer.")
    _patch_client(monkeypatch, fake_client)

    result = await llm_service.generate_answer("system", "question")

    assert result == "This is the generated answer."


# --- 4. Provider failure becomes LLMProviderError ---


async def test_generate_answer_wraps_api_error(monkeypatch):
    api_error = openai.APIError("server error", request=SimpleNamespace(), body=None)
    fake_client = _FakeClient(error=api_error)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(llm_service.LLMProviderError):
        await llm_service.generate_answer("system", "question")


async def test_generate_answer_wraps_timeout_error(monkeypatch):
    timeout_error = openai.APITimeoutError(request=SimpleNamespace())
    fake_client = _FakeClient(error=timeout_error)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(llm_service.LLMProviderError):
        await llm_service.generate_answer("system", "question")


async def test_generate_answer_wraps_connection_error(monkeypatch):
    connection_error = openai.APIConnectionError(request=SimpleNamespace())
    fake_client = _FakeClient(error=connection_error)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(llm_service.LLMProviderError):
        await llm_service.generate_answer("system", "question")


async def test_generate_answer_wraps_client_construction_error(monkeypatch):
    """
    An OpenAIError raised while constructing the client itself (e.g.
    an authentication/configuration failure inside _get_client()) must
    also become LLMProviderError — not propagate as a raw
    openai.OpenAIError. _get_client() is still the existing function
    the service calls; here it's replaced with a version that raises,
    proving the call site (not just the API request) is covered by
    the same try/except.
    """
    auth_error = openai.AuthenticationError(
        "invalid api key",
        response=SimpleNamespace(
            status_code=401, request=SimpleNamespace(), headers={}
        ),
        body=None,
    )

    def _failing_get_client():
        raise auth_error

    monkeypatch.setattr(llm_service, "_get_client", _failing_get_client)

    with pytest.raises(llm_service.LLMProviderError):
        await llm_service.generate_answer("system", "question")


# --- 5. No provider details are leaked ---


async def test_generate_answer_does_not_leak_provider_details(monkeypatch):
    api_error = openai.APIError(
        "sensitive internal provider detail with request id abc123",
        request=SimpleNamespace(),
        body=None,
    )
    fake_client = _FakeClient(error=api_error)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(llm_service.LLMProviderError) as exc_info:
        await llm_service.generate_answer("system", "question")

    assert "sensitive internal provider detail" not in str(exc_info.value)
    assert "abc123" not in str(exc_info.value)


async def test_generate_answer_handles_malformed_response_safely(monkeypatch):
    fake_client = _FakeClient(content=None)
    # Simulate a response with no choices at all — a malformed/
    # unexpected provider response shape.
    async def _malformed_create(*, model, messages):
        return SimpleNamespace(choices=[])

    fake_client.chat.completions.create = _malformed_create
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(llm_service.LLMProviderError):
        await llm_service.generate_answer("system", "question")


async def test_generate_answer_handles_none_content_safely(monkeypatch):
    fake_client = _FakeClient(content=None)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(llm_service.LLMProviderError):
        await llm_service.generate_answer("system", "question")


# --- 6. Tests work without OPENAI_API_KEY ---


async def test_generate_answer_works_without_real_api_key(monkeypatch):
    # settings.openai_api_key is "" by default in this test environment
    # (see app/core/config.py) — confirm the call still succeeds since
    # the client is never real, only the fake stand-in constructed by
    # _patch_client.
    fake_client = _FakeClient(content="ok")
    _patch_client(monkeypatch, fake_client)

    result = await llm_service.generate_answer("system", "question")

    assert result == "ok"
