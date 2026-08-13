"""
Chunk service.

Document Chunking milestone scope: transforms DocumentText.content
into deterministic, ordered DocumentChunk rows — the first thing that
consumes extracted text for anything beyond raw storage. Mirrors
parse_service.py's decoupling philosophy: chunk_text() is a pure
function, completely independent of FastAPI, HTTP, SQLAlchemy, or any
model, so it's testable in total isolation and reusable anywhere.

Deliberately exposes only chunk_text() (pure) and _replace_chunks()
(persistence primitive, no commit) as its public surface — no
standalone self-committing chunk_and_store_document_text() exists,
since no real caller needs independent chunk persistence today; the
only caller is document_processing_service.process_document(), which
owns the single commit spanning both text, chunk, and embedding
persistence. See PROJECT_CONTEXT.md for the full rationale (avoiding
a speculative public API with no real second caller).

Document Chunks -> Embeddings milestone: chunk_text() itself is
UNCHANGED — the deterministic splitting algorithm is not touched by
this milestone. _replace_chunks() changes, though: it no longer calls
chunk_text() internally. Instead it accepts already-computed chunk
texts AND their already-computed embeddings, constructing every
DocumentChunk with both set at ORM-object-construction time, before
any flush. This is required because DocumentChunk.embedding is
NOT NULL — Postgres enforces that at flush/INSERT time, so a chunk
row can never be flushed (even transiently, mid-transaction) without
its embedding already attached. The caller
(document_processing_service.process_document()) is responsible for
calling chunk_text() and embedding_service.embed_texts() itself,
before calling this function.
"""
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_chunk import DocumentChunk
from app.models.document_text import DocumentText

_PARAGRAPH_SEPARATOR = "\n\n"
_TARGET = 1000
_MAX = 1200
_OVERLAP = 150
_WHITESPACE_LOOKBACK = 20


def chunk_text(content: str) -> list[str]:
    """
    Split `content` into deterministic, ordered chunks.

    Algorithm (fully specified — see the Document Chunking design
    review for the full rationale and worked examples):

    1. Split on "\\n\\n" (the same paragraph-boundary marker
       parse_service.extract_text() already produces between pages);
       strip each piece; drop empty ones. Empty/whitespace-only input
       produces zero chunks.

    2. Greedily combine consecutive paragraphs into a chunk as long
       as the combined length (paragraphs joined by "\\n\\n") stays
       <= TARGET (1000 chars). A paragraph that doesn't fit closes
       the current chunk and starts a new one. A normal
       paragraph-combined chunk can therefore never exceed TARGET.

    3. A single paragraph longer than MAX (1200 chars) is hard-split
       into TARGET-sized windows, each overlapping the previous by
       OVERLAP (150) characters, so context isn't lost across an
       artificial mid-paragraph cut. A paragraph between TARGET and
       MAX is kept intact as its own chunk, not split — it's under
       MAX, so it doesn't need the split-plus-overlap treatment.

    4. Each hard-split window's cut point snaps backward (up to
       WHITESPACE_LOOKBACK=20 chars) to the nearest whitespace, to
       avoid splitting mid-word where reasonably possible. If no
       whitespace exists within that lookback (e.g. one very long
       token/URL), it falls back to a raw character-count cut,
       deterministically.

    5. chunk_index is the emission order (already document order,
       since paragraphs are processed left-to-right and a paragraph's
       hard-split pieces are emitted left-to-right within it) — but
       this function itself only returns strings in order; the
       caller (_replace_chunks) assigns chunk_index.

    No tokenizer, no NLP library, no semantic/embedding-based
    chunking — character-count-based only, deliberately, per the
    approved design (no dependency the rest of the project doesn't
    already need).
    """
    paragraphs = [p.strip() for p in content.split(_PARAGRAPH_SEPARATOR)]
    paragraphs = [p for p in paragraphs if p]
    if not paragraphs:
        return []

    chunks: list[str] = []
    buffer = ""

    def flush_buffer() -> None:
        nonlocal buffer
        if buffer:
            chunks.append(buffer)
            buffer = ""

    for paragraph in paragraphs:
        if not buffer:
            if len(paragraph) > _MAX:
                chunks.extend(_hard_split(paragraph))
            else:
                buffer = paragraph
            continue

        candidate_len = len(buffer) + len(_PARAGRAPH_SEPARATOR) + len(paragraph)
        if candidate_len <= _TARGET:
            buffer = buffer + _PARAGRAPH_SEPARATOR + paragraph
        else:
            flush_buffer()
            if len(paragraph) > _MAX:
                chunks.extend(_hard_split(paragraph))
            else:
                buffer = paragraph

    flush_buffer()
    return chunks


def _hard_split(paragraph: str) -> list[str]:
    """Splits a single paragraph longer than MAX into overlapping,
    whitespace-snapped, TARGET-sized windows. See chunk_text()'s
    docstring for the full algorithm description."""
    pieces: list[str] = []
    length = len(paragraph)
    window_start = 0

    while window_start < length:
        window_end = min(window_start + _TARGET, length)

        if window_end < length and not paragraph[window_end].isspace():
            lookback_floor = max(window_start, window_end - _WHITESPACE_LOOKBACK)
            snap_index = paragraph.rfind(" ", lookback_floor, window_end)
            if snap_index != -1:
                window_end = snap_index

        piece = paragraph[window_start:window_end].strip()
        if piece:
            pieces.append(piece)

        if window_end >= length:
            break
        window_start = window_end - _OVERLAP

    return pieces


async def _replace_chunks(
    db: AsyncSession,
    *,
    document_text: DocumentText,
    chunk_texts: list[str],
    embeddings: list[list[float]],
) -> list[DocumentChunk]:
    """
    Deletes any existing chunks for `document_text` and inserts new
    ones built from the given `chunk_texts`/`embeddings` — flush
    only, no commit. Reprocessing strategy: delete-then-recreate (not
    update-in-place), since chunk *count* changes when content
    changes and there's no stable 1:1 row correspondence to update
    against.

    `chunk_texts` and `embeddings` must already be the same length,
    in the same order — the caller (document_processing_service) is
    responsible for having produced them together (chunk_text(), then
    embedding_service.embed_texts() on that exact list). This
    function raises ValueError on a mismatch rather than silently
    zipping a short list, since that would mean pairing the wrong
    embedding with the wrong chunk — a correctness bug, not a
    provider/runtime failure, so it isn't wrapped in
    EmbeddingProviderError.

    Deliberately not committed here: the caller
    (document_processing_service.process_document()) owns the single
    transaction spanning DocumentText, DocumentChunk, and embedding
    persistence together, so all of it becomes durable together or
    not at all. Every constructed DocumentChunk already has its
    `embedding` set before this function's own flush — required,
    since the column is NOT NULL and Postgres enforces that at
    flush/INSERT time, not just at commit.
    """
    if len(chunk_texts) != len(embeddings):
        raise ValueError(
            f"chunk_texts and embeddings must be the same length "
            f"(got {len(chunk_texts)} texts and {len(embeddings)} embeddings)"
        )

    await db.execute(
        delete(DocumentChunk).where(DocumentChunk.document_text_id == document_text.id)
    )

    chunks = [
        DocumentChunk(
            document_text_id=document_text.id,
            chunk_index=index,
            content=text,
            embedding=embedding,
        )
        for index, (text, embedding) in enumerate(zip(chunk_texts, embeddings))
    ]
    db.add_all(chunks)
    await db.flush()
    return chunks
