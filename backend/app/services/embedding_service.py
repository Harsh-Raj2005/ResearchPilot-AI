"""
Embedding service.

Document Chunks -> Embeddings milestone: the single place the OpenAI
SDK is instantiated or called anywhere in this codebase. Mirrors the
same "wrap one external capability behind a small, focused module"
shape already established by parse_service.py (wraps PyMuPDF) and
storage_service.py (wraps the filesystem) — a real, existing
architectural pattern, not a new one invented for this milestone.

Deliberately minimal public surface: one function, embed_texts().
No provider interface, no abstract base class, no registry, no
strategy pattern — there is exactly one provider and one real caller
(document_processing_service.process_document()), so an abstraction
layer would have no second implementation or second caller to justify
it, per this project's standing "no speculative abstraction" principle.

Model/dimensions are read from settings (app.core.config), never
hard-coded in more than this one place.
"""
from openai import AsyncOpenAI, OpenAIError

from app.core.config import settings


class EmbeddingProviderError(Exception):
    """
    Raised for any embedding-provider failure — API errors, network
    errors, timeouts, authentication failures, or a malformed/
    unexpected response (e.g. a mismatched embedding count). Wraps
    the underlying openai.OpenAIError (or a local validation failure)
    without leaking raw provider exception text, response bodies, or
    the API key to callers — mirrors this codebase's existing pattern
    of translating a third-party library's exceptions into one
    project-owned domain exception (see parse_service.ParseError,
    storage_service.StorageError).
    """


def _get_client() -> AsyncOpenAI:
    """
    Constructs a client per call rather than a module-level singleton,
    so tests can freely patch app.core.config.settings (e.g. via
    monkeypatch) without needing to reset cached client state.
    """
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Returns one embedding vector per input text, in the same order as
    `texts`. Empty input returns an empty list without making any API
    call — the caller (document_processing_service) already handles
    the empty-chunks case by not invoking this function at all, but
    this function stays correct standalone regardless.

    Batches every text into a single API request — the OpenAI
    embeddings endpoint accepts an array input, so a document's full
    set of chunk texts is embedded in one round-trip rather than one
    call per chunk (see the Embeddings Design Review's Phase 11 for
    the batching rationale). No sub-batching/chunked-request logic is
    implemented — Phase 1's single-document scope makes this
    extremely unlikely to hit any provider batch-size limit; revisit
    only if real evidence shows otherwise.

    Raises EmbeddingProviderError for any provider/network/timeout
    failure, or if the provider's response doesn't contain exactly
    one embedding per input text (a defensive check — the ordering
    and count returned by the API should always match the input, but
    this function doesn't trust that silently).
    """
    if not texts:
        return []

    client = _get_client()
    try:
        response = await client.embeddings.create(
            input=texts,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    except OpenAIError as exc:
        raise EmbeddingProviderError("Embedding generation failed.") from exc

    try:
        embeddings = [
            item.embedding for item in sorted(response.data, key=lambda item: item.index)
        ]
    except (AttributeError, TypeError, KeyError) as exc:
        raise EmbeddingProviderError(
            "Embedding provider returned an unexpected response format."
        ) from exc

    if len(embeddings) != len(texts):
        raise EmbeddingProviderError(
            "Embedding provider returned a mismatched number of embeddings."
        )

    return embeddings


async def embed_query(text: str) -> list[float]:
    """
    Returns a single embedding vector for one query string.

    A thin convenience wrapper around embed_texts() — a query is just
    one text, so this reuses the exact same batching/error-translation
    logic rather than duplicating it or introducing a second call path
    to the provider. Raises EmbeddingProviderError under the same
    conditions embed_texts() does; see that function's docstring.
    """
    embeddings = await embed_texts([text])
    return embeddings[0]
