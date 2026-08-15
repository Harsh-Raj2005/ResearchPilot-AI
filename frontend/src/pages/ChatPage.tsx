/**
 * Chat page — Frontend Chat UI milestone. The document-scoped chat
 * workspace consuming the existing Chat Persistence backend API
 * (session create/list, message send/list). Deliberately a single
 * file, matching DocumentsPage.tsx's one-file-per-feature precedent
 * — no premature component extraction.
 *
 * Server is the source of truth throughout: no client-side
 * conversation store. Every send/refresh/session-switch re-fetches
 * from the API; React state only holds the current UI snapshot.
 *
 * Deliberately out of scope (see Chat Persistence's own
 * PROJECT_CONTEXT.md decisions, unchanged here): streaming, message
 * editing/deletion, session titles/renaming, citations, PDF viewer,
 * conversation summarization.
 */
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ROUTES } from "../constants/routes";
import { ApiError } from "../services/api";
import * as chatService from "../services/chat";
import * as documentService from "../services/document";
import type { ChatMessageResponse, ChatSessionResponse } from "../types/chat";
import type { DocumentResponse } from "../types/document";

function formatTime(isoString: string): string {
  return new Date(isoString).toLocaleString();
}

export default function ChatPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [document, setDocument] = useState<DocumentResponse | null>(null);
  const [documentError, setDocumentError] = useState<string | null>(null);

  const [sessions, setSessions] = useState<ChatSessionResponse[]>([]);
  const [isLoadingSessions, setIsLoadingSessions] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [isCreatingSession, setIsCreatingSession] = useState(false);

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageResponse[]>([]);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);

  const [question, setQuestion] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const handleAuthFailure = useCallback(
    (err: unknown): boolean => {
      if (err instanceof ApiError && err.status === 401) {
        logout();
        navigate(ROUTES.login);
        return true;
      }
      return false;
    },
    [logout, navigate]
  );

  // --- Load document metadata (for the header) ---
  useEffect(() => {
    if (!token || !documentId) return;
    documentService
      .getDocument(token, documentId)
      .then(setDocument)
      .catch((err: unknown) => {
        if (!handleAuthFailure(err)) {
          setDocumentError(
            err instanceof ApiError && err.status === 404
              ? "Document not found."
              : err instanceof Error
              ? err.message
              : "Failed to load document"
          );
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, documentId]);

  // --- Load sessions ---
  const fetchSessions = useCallback(async () => {
    if (!token || !documentId) return;
    setIsLoadingSessions(true);
    setSessionsError(null);
    try {
      const list = await chatService.listChatSessions(token, documentId);
      setSessions(list);
      return list;
    } catch (err) {
      if (!handleAuthFailure(err)) {
        setSessionsError(
          err instanceof ApiError && err.status === 404
            ? "Document not found."
            : err instanceof Error
            ? err.message
            : "Failed to load chat sessions"
        );
      }
      return [];
    } finally {
      setIsLoadingSessions(false);
    }
  }, [token, documentId, handleAuthFailure]);

  useEffect(() => {
    void fetchSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Load messages for the selected session ---
  const fetchMessages = useCallback(
    async (sessionId: string) => {
      if (!token || !documentId) return;
      setIsLoadingMessages(true);
      setMessagesError(null);
      try {
        const list = await chatService.getChatMessages(token, documentId, sessionId);
        setMessages(list);
      } catch (err) {
        if (!handleAuthFailure(err)) {
          setMessagesError(
            err instanceof ApiError && err.status === 404
              ? "This conversation could not be found."
              : err instanceof Error
              ? err.message
              : "Failed to load messages"
          );
        }
      } finally {
        setIsLoadingMessages(false);
      }
    },
    [token, documentId, handleAuthFailure]
  );

  function selectSession(sessionId: string) {
    setSelectedSessionId(sessionId);
    setSendError(null);
    void fetchMessages(sessionId);
  }

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  async function handleNewChat() {
    if (!token || !documentId) return;
    setIsCreatingSession(true);
    setSessionsError(null);
    try {
      const session = await chatService.createChatSession(token, documentId);
      setSessions((previous) => [session, ...previous]);
      setMessages([]);
      setSelectedSessionId(session.id);
    } catch (err) {
      if (!handleAuthFailure(err)) {
        setSessionsError(err instanceof Error ? err.message : "Failed to create a new chat");
      }
    } finally {
      setIsCreatingSession(false);
    }
  }

  async function handleSend(event?: FormEvent) {
    event?.preventDefault();
    if (!token || !documentId || !selectedSessionId) return;
    const trimmed = question.trim();
    if (!trimmed || isSending) return;

    setIsSending(true);
    setSendError(null);
    // Optimistic pending user turn — the server owns the real
    // persisted record; this is only a UI placeholder replaced once
    // the request settles (matches the project's "server is the
    // source of truth" persistence rule below).
    const pendingUserMessage: ChatMessageResponse = {
      id: `pending-${Date.now()}`,
      role: "user",
      content: trimmed,
      sequence_number: -1,
      created_at: new Date().toISOString(),
    };
    setMessages((previous) => [...previous, pendingUserMessage]);
    setQuestion("");

    try {
      await chatService.sendChatMessage(token, documentId, selectedSessionId, trimmed);
      // Re-fetch the real, persisted transcript rather than trusting
      // the optimistic placeholder — the server assigns the real
      // sequence numbers and IDs.
      await fetchMessages(selectedSessionId);
    } catch (err) {
      // Remove the optimistic placeholder on failure so the UI never
      // shows a question that was never actually persisted.
      setMessages((previous) => previous.filter((m) => m.id !== pendingUserMessage.id));
      if (!handleAuthFailure(err)) {
        if (err instanceof ApiError && err.status === 404) {
          setSendError("This conversation could not be found.");
        } else if (err instanceof ApiError && err.status === 422) {
          setSendError(err.message || "That question isn't valid.");
        } else if (err instanceof ApiError && err.status === 502) {
          setSendError("The assistant couldn't generate a response. Please try again.");
        } else {
          setSendError(err instanceof Error ? err.message : "Failed to send message");
        }
      }
    } finally {
      setIsSending(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  }

  return (
    <main style={{ display: "flex", flexDirection: "column", height: "100svh", fontFamily: "var(--sans)" }}>
      <header
        style={{
          padding: "0.75rem 1.5rem",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: "1rem",
        }}
      >
        <button onClick={() => navigate(ROUTES.documents)} style={{ flexShrink: 0 }}>
          &larr; Documents
        </button>
        <h2 style={{ margin: 0, fontSize: "1.1rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {documentError ? documentError : document ? document.original_filename : "Loading..."}
        </h2>
      </header>

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* Session list sidebar */}
        <aside
          style={{
            width: "260px",
            flexShrink: 0,
            borderRight: "1px solid var(--border)",
            padding: "1rem",
            overflowY: "auto",
          }}
        >
          <button onClick={handleNewChat} disabled={isCreatingSession} style={{ width: "100%", marginBottom: "1rem" }}>
            {isCreatingSession ? "Creating..." : "+ New Chat"}
          </button>

          {isLoadingSessions && <p>Loading conversations...</p>}
          {sessionsError && <p style={{ color: "crimson" }}>{sessionsError}</p>}
          {!isLoadingSessions && !sessionsError && sessions.length === 0 && (
            <p>No conversations yet.</p>
          )}

          {sessions.map((session) => (
            <div
              key={session.id}
              onClick={() => selectSession(session.id)}
              style={{
                padding: "0.5rem",
                borderRadius: "4px",
                cursor: "pointer",
                marginBottom: "0.25rem",
                background: session.id === selectedSessionId ? "var(--accent-bg)" : "transparent",
                border:
                  session.id === selectedSessionId
                    ? "1px solid var(--accent-border)"
                    : "1px solid transparent",
              }}
            >
              Conversation started {formatTime(session.created_at)}
            </div>
          ))}
        </aside>

        {/* Conversation panel */}
        <section style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          {!selectedSessionId && (
            <div style={{ margin: "auto", color: "var(--text)" }}>
              <p>Select a conversation, or start a new chat.</p>
            </div>
          )}

          {selectedSessionId && (
            <>
              <div style={{ flex: 1, overflowY: "auto", padding: "1rem 1.5rem" }}>
                {isLoadingMessages && <p>Loading messages...</p>}
                {messagesError && <p style={{ color: "crimson" }}>{messagesError}</p>}
                {!isLoadingMessages && !messagesError && messages.length === 0 && (
                  <p>Ask a question about this paper.</p>
                )}

                {messages.map((message) => (
                  <div
                    key={message.id}
                    style={{
                      display: "flex",
                      justifyContent: message.role === "user" ? "flex-end" : "flex-start",
                      marginBottom: "0.75rem",
                    }}
                  >
                    <div
                      style={{
                        maxWidth: "70%",
                        padding: "0.6rem 0.9rem",
                        borderRadius: "8px",
                        background: message.role === "user" ? "var(--accent-bg)" : "var(--code-bg)",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        textAlign: "left",
                      }}
                    >
                      {message.content}
                    </div>
                  </div>
                ))}
                <div ref={messagesEndRef} />
              </div>

              <form
                onSubmit={handleSend}
                style={{
                  borderTop: "1px solid var(--border)",
                  padding: "0.75rem 1.5rem",
                  display: "flex",
                  gap: "0.5rem",
                }}
              >
                <textarea
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  placeholder="Ask a question..."
                  rows={1}
                  disabled={isSending}
                  style={{ flex: 1, resize: "none", padding: "0.5rem" }}
                />
                <button type="submit" disabled={isSending || !question.trim()}>
                  {isSending ? "Sending..." : "Send"}
                </button>
              </form>
              {sendError && (
                <p style={{ color: "crimson", padding: "0 1.5rem 0.5rem" }}>{sendError}</p>
              )}
            </>
          )}
        </section>
      </div>
    </main>
  );
}
