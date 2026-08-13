"""
Tests for app.services.embedding_service.

No real OpenAI API call is ever made — every test monkeypatches
embedding_service._get_client() to return a fake client whose
`.embeddings.create()` is under full test control. No OPENAI_API_KEY
or network access is required to run this file.
"""
from types import SimpleNamespace

import openai
import pytest

from app.services import embedding_service


class _FakeEmbeddingItem:
    def __init__(self, embedding: list[float], index: int):
        self.embedding = embedding
        self.index = index


class _FakeEmbeddingsResource:
    """Stands in for client.embeddings — records calls, returns a
    pre-configured response or raises a pre-configured exception."""

    def __init__(self, *, data=None, error=None):
        self._data = data
        self._error = error
        self.calls: list[dict] = []

    async def create(self, *, input, model, dimensions):
        self.calls.append({"input": input, "model": model, "dimensions": dimensions})
        if self._error is not None:
            raise self._error
        return SimpleNamespace(data=self._data)


class _FakeClient:
    def __init__(self, *, data=None, error=None):
        self.embeddings = _FakeEmbeddingsResource(data=data, error=error)


def _patch_client(monkeypatch, fake_client):
    monkeypatch.setattr(embedding_service, "_get_client", lambda: fake_client)


# --- 1. Successful embedding generation ---


async def test_embed_texts_returns_one_vector_per_input(monkeypatch):
    fake_data = [
        _FakeEmbeddingItem(embedding=[0.1, 0.2], index=0),
        _FakeEmbeddingItem(embedding=[0.3, 0.4], index=1),
    ]
    fake_client = _FakeClient(data=fake_data)
    _patch_client(monkeypatch, fake_client)

    result = await embedding_service.embed_texts(["first chunk", "second chunk"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    assert fake_client.embeddings.calls[0]["input"] == ["first chunk", "second chunk"]
    assert fake_client.embeddings.calls[0]["model"] == embedding_service.settings.embedding_model
    assert (
        fake_client.embeddings.calls[0]["dimensions"]
        == embedding_service.settings.embedding_dimensions
    )


# --- 2. Multiple texts preserve ordering, even if the provider returns them out of order ---


async def test_embed_texts_preserves_order_despite_shuffled_response(monkeypatch):
    fake_data = [
        _FakeEmbeddingItem(embedding=[9.0], index=2),
        _FakeEmbeddingItem(embedding=[7.0], index=0),
        _FakeEmbeddingItem(embedding=[8.0], index=1),
    ]
    fake_client = _FakeClient(data=fake_data)
    _patch_client(monkeypatch, fake_client)

    result = await embedding_service.embed_texts(["a", "b", "c"])

    assert result == [[7.0], [8.0], [9.0]]


# --- 3. Returned count mismatch is rejected ---


async def test_embed_texts_rejects_mismatched_embedding_count(monkeypatch):
    fake_data = [_FakeEmbeddingItem(embedding=[0.1], index=0)]  # only 1, but 2 texts sent
    fake_client = _FakeClient(data=fake_data)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(embedding_service.EmbeddingProviderError):
        await embedding_service.embed_texts(["first", "second"])


# --- 4. Provider/API error becomes EmbeddingProviderError ---


async def test_embed_texts_wraps_api_error(monkeypatch):
    api_error = openai.APIError(
        "server error", request=SimpleNamespace(), body=None
    )
    fake_client = _FakeClient(error=api_error)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(embedding_service.EmbeddingProviderError):
        await embedding_service.embed_texts(["some text"])


# --- 5. Timeout/network failure becomes EmbeddingProviderError ---


async def test_embed_texts_wraps_timeout_error(monkeypatch):
    timeout_error = openai.APITimeoutError(request=SimpleNamespace())
    fake_client = _FakeClient(error=timeout_error)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(embedding_service.EmbeddingProviderError):
        await embedding_service.embed_texts(["some text"])


async def test_embed_texts_wraps_connection_error(monkeypatch):
    connection_error = openai.APIConnectionError(request=SimpleNamespace())
    fake_client = _FakeClient(error=connection_error)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(embedding_service.EmbeddingProviderError):
        await embedding_service.embed_texts(["some text"])


# --- 6. Malformed/unexpected provider response is handled safely ---


async def test_embed_texts_handles_malformed_response_safely(monkeypatch):
    # Response items missing the expected `.embedding`/`.index` shape
    # entirely (e.g. a provider contract change) — must not raise a
    # raw AttributeError out of this module.
    fake_data = [SimpleNamespace(unexpected_field="oops")]
    fake_client = _FakeClient(data=fake_data)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(embedding_service.EmbeddingProviderError):
        await embedding_service.embed_texts(["some text"])


# --- 7. Empty input behavior ---


async def test_embed_texts_empty_input_returns_empty_list_no_call(monkeypatch):
    def _fail_if_called():
        raise AssertionError("embedding client should not be constructed for empty input")

    monkeypatch.setattr(embedding_service, "_get_client", _fail_if_called)

    result = await embedding_service.embed_texts([])

    assert result == []


# --- 8. embed_query() — single-text convenience wrapper ---


async def test_embed_query_returns_single_vector_with_correct_text_model_dimensions(monkeypatch):
    fake_data = [_FakeEmbeddingItem(embedding=[0.5, 0.6], index=0)]
    fake_client = _FakeClient(data=fake_data)
    _patch_client(monkeypatch, fake_client)

    result = await embedding_service.embed_query("what does the paper conclude?")

    assert result == [0.5, 0.6]
    assert fake_client.embeddings.calls[0]["input"] == ["what does the paper conclude?"]
    assert fake_client.embeddings.calls[0]["model"] == embedding_service.settings.embedding_model
    assert (
        fake_client.embeddings.calls[0]["dimensions"]
        == embedding_service.settings.embedding_dimensions
    )


async def test_embed_query_provider_failure_becomes_embedding_provider_error(monkeypatch):
    api_error = openai.APIError("server error", request=SimpleNamespace(), body=None)
    fake_client = _FakeClient(error=api_error)
    _patch_client(monkeypatch, fake_client)

    with pytest.raises(embedding_service.EmbeddingProviderError):
        await embedding_service.embed_query("some query")
