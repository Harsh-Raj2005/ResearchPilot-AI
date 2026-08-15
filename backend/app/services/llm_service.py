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
this milestone's exact shape (system instruction + a list of turns ->
text answer); nothing here needs tool calling or agentic behavior.

model is read from settings.llm_model — a distinct setting from
settings.embedding_model (the embedding and generation models are
never the same model and must never be confused).

Chat Persistence milestone: generate_answer()'s signature changed
from (system_prompt, user_prompt) to (system_prompt, messages) — a
real, deliberate signature change, not a purely additive one. This
module now uses OpenAI's actual multi-turn mechanism (a list of
{"role", "content"} turns) rather than folding conversation history
into one large string, which would have relabeled prior assistant
turns as generic "context" and lost the model's native structure for
distinguishing them. rag_service.answer_question() (the existing
stateless single-question path) is unchanged in behavior — it now
calls this function with messages=[{"role": "user", "content":
user_prompt}], a one-element list, which is behaviorally identical to
the old two-message shape this function used to build internally.
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


async def generate_answer(system_prompt: str, messages: list[dict[str, str]]) -> str:
    """
    Sends a system instruction plus a list of conversation turns to
    the configured generation model and returns the generated answer
    text.

    `messages` is a list of {"role": "user"|"assistant", "content":
    str} dicts, in chronological order — the full conversation history
    (already truncated/selected by the caller, e.g. rag_service) plus
    the current turn as its final element. This function does not
    truncate, reorder, or otherwise interpret `messages` itself; it
    only prepends the system prompt and sends exactly what it's given.
    A single-question caller (no history) simply passes a one-element
    list: [{"role": "user", "content": the_question_or_prompt}].

    This function has no knowledge of documents, chunks, retrieval,
    RAG, or chat sessions — it is a narrow, generic "send this
    system prompt and message list, get this answer" boundary. All
    prompt construction (grounding rules, context formatting, history
    selection) is the caller's responsibility, not this module's.

    Raises LLMProviderError for any provider/network/timeout failure
    (including a failure raised while constructing the client itself),
    or if the response doesn't contain the expected generated text (a
    defensive check — mirrors embedding_service.embed_texts()'s
    equivalent malformed-response handling).
    """
    if isinstance(messages, str):
        # A bare string is technically iterable, so without this check
        # `*messages` below would silently unpack it character-by-
        # character into garbage "messages" instead of raising a clear
        # error — a real footgun for any caller migrating from the
        # old (system_prompt, user_prompt: str) signature. Fail loudly
        # and immediately instead.
        raise TypeError(
            "generate_answer() expects messages: list[dict[str, str]], not a raw string"
        )

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "system", "content": system_prompt}, *messages],
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
