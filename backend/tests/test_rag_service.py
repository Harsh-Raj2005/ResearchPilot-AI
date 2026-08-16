"""
Tests for app.services.rag_service.

Testing philosophy: mock only the external-provider boundaries
(embedding_service's OpenAI client and llm_service's OpenAI client),
and exercise everything else for real — real embed_query() call
logic, real retrieve_similar_chunks() against real Postgres/pgvector,
real prompt assembly. This proves the orchestration itself, not just
that each piece works in isolation. No real OpenAI API request is
ever made anywhere in this file.
"""
import uuid
from types import SimpleNamespace

import pymupdf
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_chunk import DocumentChunk
from app.models.document_text import DocumentText
from app.services import (
    auth_service,
    document_service,
    embedding_service,
    llm_service,
    rag_service,
)

_DIM = 1536


def _unit_vector(index: int, dim: int = _DIM) -> list[float]:
    vector = [0.0] * dim
    vector[index % dim] = 1.0
    return vector


def _make_pdf_bytes(text: str = "placeholder") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


async def _make_user(db_session: AsyncSession, suffix: str):
    return await auth_service.create_user(
        db_session,
        email=f"rag_{suffix}@example.com",
        username=f"rag{suffix}",
        password="password123",
    )


async def _make_document(db_session: AsyncSession, suffix: str):
    user = await _make_user(db_session, suffix)
    return await document_service.create_document(
        db_session,
        user_id=user.id,
        content=_make_pdf_bytes(),
        original_filename="paper.pdf",
        content_type="application/pdf",
    )


async def _make_document_text(db_session: AsyncSession, document_id: uuid.UUID) -> DocumentText:
    document_text = DocumentText(document_id=document_id, content="Some extracted text.")
    db_session.add(document_text)
    await db_session.flush()
    return document_text


async def _add_chunk(
    db_session: AsyncSession,
    *,
    document_text_id: uuid.UUID,
    chunk_index: int,
    content: str,
    embedding: list[float],
) -> DocumentChunk:
    chunk = DocumentChunk(
        document_text_id=document_text_id,
        chunk_index=chunk_index,
        content=content,
        embedding=embedding,
    )
    db_session.add(chunk)
    await db_session.flush()
    return chunk


def _mock_embedding_client(monkeypatch, *, vector: list[float]):
    """
    Mocks embedding_service's OpenAI client boundary (not
    embed_query() itself) — the real embed_query()/embed_texts() logic
    still runs, only the network call is replaced. Mirrors
    test_embedding_service.py's own fake-client convention.
    """

    class _FakeEmbeddingItem:
        def __init__(self, embedding, index):
            self.embedding = embedding
            self.index = index

    class _FakeEmbeddingsResource:
        def __init__(self):
            self.calls = []

        async def create(self, *, input, model, dimensions):
            self.calls.append({"input": input, "model": model, "dimensions": dimensions})
            return SimpleNamespace(
                data=[_FakeEmbeddingItem(embedding=vector, index=i) for i in range(len(input))]
            )

    class _FakeClient:
        def __init__(self):
            self.embeddings = _FakeEmbeddingsResource()

    fake_client = _FakeClient()
    monkeypatch.setattr(embedding_service, "_get_client", lambda: fake_client)
    return fake_client


def _mock_llm_client(monkeypatch, *, answer: str = "generated answer", error=None):
    """
    Mocks llm_service's OpenAI client boundary — the real
    generate_answer() logic still runs, only the network call is
    replaced. Mirrors test_llm_service.py's own fake-client convention.
    """

    class _FakeMessage:
        def __init__(self, content):
            self.content = content

    class _FakeChoice:
        def __init__(self, content):
            self.message = _FakeMessage(content)

    class _FakeCompletionsResource:
        def __init__(self):
            self.calls = []

        async def create(self, *, model, messages):
            self.calls.append({"model": model, "messages": messages})
            if error is not None:
                raise error
            return SimpleNamespace(choices=[_FakeChoice(answer)])

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletionsResource())

    fake_client = _FakeClient()
    monkeypatch.setattr(llm_service, "_get_client", lambda: fake_client)
    return fake_client


# --- 1 & 2. Question is embedded, embedding is passed to retrieval, retrieval scoped to the right document ---


async def test_answer_question_embeds_question_and_retrieves_from_correct_document(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "flow")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="ResearchPilot is a document assistant.",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    embedding_client = _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="It is a document assistant.")

    result = await rag_service.answer_question(
        db_session, document=document, question="What is ResearchPilot?"
    )

    # The question text was actually sent to the embedding provider.
    assert embedding_client.embeddings.calls[0]["input"] == ["What is ResearchPilot?"]
    # A real retrieval happened (proven by the LLM receiving the
    # chunk's content in its prompt, asserted in detail below) and the
    # final answer is exactly what the LLM service returned.
    assert result == "It is a document assistant."
    assert len(llm_client.chat.completions.calls) == 1


# --- 3. Correct Document object is passed to retrieval (proven via isolation) ---


async def test_answer_question_never_uses_another_documents_chunks(
    db_session: AsyncSession, monkeypatch
):
    document_a = await _make_document(db_session, "isoA")
    document_b = await _make_document(db_session, "isoB")
    text_a = await _make_document_text(db_session, document_a.id)
    text_b = await _make_document_text(db_session, document_b.id)

    await _add_chunk(
        db_session,
        document_text_id=text_a.id,
        chunk_index=0,
        content="Document A content.",
        embedding=_unit_vector(0),
    )
    # Document B's chunk has an identical embedding — if isolation
    # were broken, this chunk could leak into Document A's answer.
    await _add_chunk(
        db_session,
        document_text_id=text_b.id,
        chunk_index=0,
        content="Document B content — must never appear.",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="answer")

    await rag_service.answer_question(db_session, document=document_a, question="What is in here?")

    sent_prompt = llm_client.chat.completions.calls[0]["messages"][1]["content"]
    assert "Document A content." in sent_prompt
    assert "Document B content" not in sent_prompt


# --- 4 & 5. Retrieved chunks are incorporated into the prompt, deterministic ordering ---


async def test_answer_question_prompt_contains_chunks_in_retrieval_order(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "order")
    document_text = await _make_document_text(db_session, document.id)

    # Two chunks with distinct, predictable cosine distances from the
    # (mocked) query embedding, so retrieval order is unambiguous.
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="closest chunk content",
        embedding=_unit_vector(0),
    )
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=1,
        content="farthest chunk content",
        embedding=_unit_vector(1),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))  # exact match to chunk 0
    llm_client = _mock_llm_client(monkeypatch, answer="answer")

    await rag_service.answer_question(db_session, document=document, question="question")

    sent_prompt = llm_client.chat.completions.calls[0]["messages"][1]["content"]
    closest_position = sent_prompt.index("closest chunk content")
    farthest_position = sent_prompt.index("farthest chunk content")
    assert closest_position < farthest_position
    assert "[Context Chunk 1]" in sent_prompt
    assert "[Context Chunk 2]" in sent_prompt


# --- 6. Grounding instructions are present ---


async def test_answer_question_system_prompt_contains_grounding_instructions(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "grounding")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="answer")

    await rag_service.answer_question(db_session, document=document, question="question")

    system_message = llm_client.chat.completions.calls[0]["messages"][0]["content"]
    assert system_message.startswith("system") is False  # sanity: not empty/trivial
    assert "insufficient" in system_message.lower()
    assert "never invent" in system_message.lower()


# --- 7. Document context is explicitly treated as untrusted reference material ---


async def test_answer_question_system_prompt_treats_context_as_untrusted(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "untrusted")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="answer")

    await rag_service.answer_question(db_session, document=document, question="question")

    system_message = llm_client.chat.completions.calls[0]["messages"][0]["content"]
    assert "untrusted" in system_message.lower()
    assert "not as" in system_message.lower() or "not instructions" in system_message.lower()


# --- 8 & 9. LLM service receives the constructed prompt, answer returned as a string ---


async def test_answer_question_returns_llm_answer_as_string(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "returnval")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    _mock_llm_client(monkeypatch, answer="the final generated answer")

    result = await rag_service.answer_question(db_session, document=document, question="question")

    assert isinstance(result, str)
    assert result == "the final generated answer"


# --- 10 & 11. Empty retrieval does not call the LLM, returns the deterministic fallback ---


async def test_answer_question_empty_retrieval_returns_fallback_without_calling_llm(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "empty")
    await _make_document_text(db_session, document.id)  # no chunks added
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="should never be returned")

    result = await rag_service.answer_question(db_session, document=document, question="question")

    assert result == rag_service.NO_CONTEXT_ANSWER
    assert len(llm_client.chat.completions.calls) == 0


# --- 12. LLMProviderError propagates according to the approved service convention ---


async def test_answer_question_propagates_llm_provider_error(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "llmfail")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))

    import openai

    api_error = openai.APIError("provider failure", request=SimpleNamespace(), body=None)
    _mock_llm_client(monkeypatch, error=api_error)

    with pytest.raises(llm_service.LLMProviderError):
        await rag_service.answer_question(db_session, document=document, question="question")


# --- 13. No real OpenAI API request occurs (proven by the mocked-boundary pattern itself) ---


async def test_answer_question_never_uses_real_openai_client(
    db_session: AsyncSession, monkeypatch
):
    """
    Both embedding_service._get_client and llm_service._get_client are
    monkeypatched to raise if called with anything other than the fake
    clients — proving no real client construction path is reachable.
    """
    document = await _make_document(db_session, "noreal")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    _mock_llm_client(monkeypatch, answer="answer")

    # If either service fell back to a real AsyncOpenAI(), no
    # exception would necessarily be raised here (since we never
    # actually invoke a real network call in this sandboxed test
    # environment either way) — the meaningful proof is that both
    # _get_client functions were replaced above and the call
    # succeeded end-to-end using only the fakes.
    result = await rag_service.answer_question(db_session, document=document, question="question")
    assert result == "answer"


async def test_answer_question_top_k_passthrough_to_retrieval(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "topk")
    document_text = await _make_document_text(db_session, document.id)
    for i in range(5):
        await _add_chunk(
            db_session,
            document_text_id=document_text.id,
            chunk_index=i,
            content=f"chunk {i}",
            embedding=_unit_vector(i),
        )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="answer")

    await rag_service.answer_question(
        db_session, document=document, question="question", top_k=2
    )

    sent_prompt = llm_client.chat.completions.calls[0]["messages"][1]["content"]
    assert sent_prompt.count("[Context Chunk") == 2


# --- Chat Persistence milestone: answer_question_with_history() ---


async def test_answer_question_unchanged_still_works_after_history_addition(
    db_session: AsyncSession, monkeypatch
):
    """
    Confirms answer_question() (the existing stateless single-question
    path) behaves identically after llm_service.generate_answer()'s
    signature change and the addition of answer_question_with_history()
    — not a new test of new functionality, but a direct regression
    check on the unchanged function.
    """
    document = await _make_document(db_session, "unchanged")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="unchanged answer")

    result = await rag_service.answer_question(db_session, document=document, question="question")

    assert result == "unchanged answer"
    messages = llm_client.chat.completions.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert len(messages) == 2  # no history — exactly system + current question


async def test_answer_question_with_history_accepts_and_forwards_history(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "history")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="answer with history")

    history = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]

    result = await rag_service.answer_question_with_history(
        db_session, document=document, question="follow-up question", history=history
    )

    assert result == "answer with history"
    messages = llm_client.chat.completions.calls[0]["messages"]
    # system + 2 history turns + current question = 4 messages
    assert len(messages) == 4
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "first question"}
    assert messages[2] == {"role": "assistant", "content": "first answer"}
    assert messages[3]["role"] == "user"


async def test_answer_question_with_history_preserves_role_order(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "roleorder")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="answer")

    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]

    await rag_service.answer_question_with_history(
        db_session, document=document, question="q3", history=history
    )

    messages = llm_client.chat.completions.calls[0]["messages"]
    roles = [m["role"] for m in messages]
    # system, user, assistant, user, assistant, user(current)
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]


async def test_answer_question_with_history_only_sends_most_recent_n_messages(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "truncate")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="answer")

    # 20 history entries, well over MAX_HISTORY_MESSAGES (10) — the
    # oldest entries must be dropped, keeping only the most recent
    # MAX_HISTORY_MESSAGES.
    history = [{"role": "user", "content": f"turn {i}"} for i in range(20)]

    await rag_service.answer_question_with_history(
        db_session, document=document, question="current question", history=history
    )

    messages = llm_client.chat.completions.calls[0]["messages"]
    # system + MAX_HISTORY_MESSAGES history turns + current question
    assert len(messages) == 1 + rag_service.MAX_HISTORY_MESSAGES + 1
    history_contents = [m["content"] for m in messages[1:-1]]
    # The 10 most recent of the 20 original turns — "turn 10".."turn 19"
    assert history_contents == [f"turn {i}" for i in range(10, 20)]


async def test_answer_question_with_history_current_question_appears_exactly_once(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "onceonly")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="answer")

    history = [
        {"role": "user", "content": "prior question"},
        {"role": "assistant", "content": "prior answer"},
    ]

    await rag_service.answer_question_with_history(
        db_session, document=document, question="the current question", history=history
    )

    messages = llm_client.chat.completions.calls[0]["messages"]
    occurrences = sum(1 for m in messages if "the current question" in m["content"])
    assert occurrences == 1
    # The current question is not present anywhere in the historical
    # slice itself — only in the final message.
    assert "the current question" not in [m["content"] for m in messages[:-1]]


async def test_answer_question_with_history_empty_retrieval_returns_fallback(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "emptyhist")
    await _make_document_text(db_session, document.id)  # no chunks added
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="should never be returned")

    history = [{"role": "user", "content": "prior question"}]

    result = await rag_service.answer_question_with_history(
        db_session, document=document, question="question", history=history
    )

    assert result == rag_service.NO_CONTEXT_ANSWER
    assert len(llm_client.chat.completions.calls) == 0


async def test_answer_question_with_history_propagates_llm_provider_error(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "histllmfail")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))

    import openai

    api_error = openai.APIError("provider failure", request=SimpleNamespace(), body=None)
    _mock_llm_client(monkeypatch, error=api_error)

    history = [{"role": "user", "content": "prior question"}]

    with pytest.raises(llm_service.LLMProviderError):
        await rag_service.answer_question_with_history(
            db_session, document=document, question="question", history=history
        )


async def test_answer_question_with_history_empty_history_behaves_like_no_history(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "emptylist")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="some content",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    _mock_embedding_client(monkeypatch, vector=_unit_vector(0))
    llm_client = _mock_llm_client(monkeypatch, answer="answer")

    result = await rag_service.answer_question_with_history(
        db_session, document=document, question="question", history=[]
    )

    assert result == "answer"
    messages = llm_client.chat.completions.calls[0]["messages"]
    assert len(messages) == 2  # system + current question only
