/**
 * Chat session/message API calls, isolated from UI components — same
 * convention as services/document.ts. Nothing here knows about
 * React; every function takes the bearer token as an explicit
 * argument.
 */
import { getAuth, postAuth, postAuthBody } from "./api";
import type { ChatMessageResponse, ChatSessionResponse } from "../types/chat";

function sessionsPath(documentId: string): string {
  return `/api/v1/documents/${documentId}/chat/sessions`;
}

function messagesPath(documentId: string, sessionId: string): string {
  return `/api/v1/documents/${documentId}/chat/sessions/${sessionId}/messages`;
}

export function createChatSession(
  token: string,
  documentId: string
): Promise<ChatSessionResponse> {
  return postAuth<ChatSessionResponse>(sessionsPath(documentId), token);
}

export function listChatSessions(
  token: string,
  documentId: string,
  { skip = 0, limit = 20 }: { skip?: number; limit?: number } = {}
): Promise<ChatSessionResponse[]> {
  return getAuth<ChatSessionResponse[]>(
    `${sessionsPath(documentId)}?skip=${skip}&limit=${limit}`,
    token
  );
}

export function getChatMessages(
  token: string,
  documentId: string,
  sessionId: string
): Promise<ChatMessageResponse[]> {
  return getAuth<ChatMessageResponse[]>(messagesPath(documentId, sessionId), token);
}

/** Sends a question; the response is the newly-created assistant message. */
export function sendChatMessage(
  token: string,
  documentId: string,
  sessionId: string,
  question: string
): Promise<ChatMessageResponse> {
  return postAuthBody<ChatMessageResponse>(messagesPath(documentId, sessionId), token, {
    question,
  });
}
