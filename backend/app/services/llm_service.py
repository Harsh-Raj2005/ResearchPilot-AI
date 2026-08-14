"""
LLM service.

RAG Foundation milestone: the single place the OpenAI Chat Completions
API is called for text generation anywhere in this codebase. Mirrors
the exact same shape already established by embedding_service.py —
one small, focused module wrapping one external capability, no
provider interface, no strategy pattern, no registry.

Deliberately minimal public surface: one function, generate_answer().
There is exactly one provider (OpenAI) and exactly one real caller
(rag_service.answer_question()), so an abstraction layer would have
no second implementation or second caller to justify it, per this
project's standing "no speculative abstraction" principle.

Uses the Chat Completions API (client.chat.completions.create), not
the newer Responses API — Chat Completions is the simplest fit for
this milestone's exact shape (system instruction + user prompt ->
text answer); nothing here needs tool calling, multi-turn state, or
agentic behavior, all of which are explicitly out of scope.

model is read from settings.llm_model — a distinct setting from
settings.embedding_model (the embedding and generation models are
never the same model and must never be confused).
"""
from openai import AsyncOpenAI, OpenAIError

from app.core.config import settings


class LLMProviderError(Exception):
    """
    Raised for any LLM-provider failure — API errors, network errors,
    timeouts, authentication failures, or an unexpected/malformed
    response. Wraps the underlying openai.OpenAIError without leaking
    raw provider exception text, response bodies, or the API key to
    callers — mirrors embedding_service.EmbeddingProviderError's
    identical translation pattern.
    """


def _get_client() -> AsyncOpenAI:
    """
    Constructs a client per call rather than a module-level singleton
    — no live client is created at import time, so importing this
    module (or the application as a whole) never requires an API key.
    Mirrors embedding_service._get_client()'s identical rationale:
    tests can freely patch app.core.config.settings without needing
    to reset cached client state.
    """
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def generate_answer(system_prompt: str, user_prompt: str) -> str:
    """
    Sends a prepared system instruction and user prompt to the
    configured generation model and returns the generated answer text.

    This function has no knowledge of documents, chunks, retrieval, or
    RAG — it is a narrow, generic "send this prompt, get this answer"
    boundary. All prompt construction (grounding rules, context
    formatting, chunk selection) is rag_service's responsibility, not
    this module's.

    Raises LLMProviderError for any provider/network/timeout failure
    (including a failure raised while constructing the client itself),
    or if the response doesn't contain the expected generated text (a
    defensive check — mirrors embedding_service.embed_texts()'s
    equivalent malformed-response handling).
    """
    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except OpenAIError as exc:
        raise LLMProviderError("Answer generation failed.") from exc

    try:
        answer = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise LLMProviderError(
            "LLM provider returned an unexpected response format."
        ) from exc

    if answer is None:
        raise LLMProviderError("LLM provider returned an empty response.")

    return answer
