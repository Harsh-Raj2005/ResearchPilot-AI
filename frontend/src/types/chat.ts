/**
 * Types mirroring the backend's app/schemas/chat.py. Kept in sync by
 * hand, same convention as types/document.ts. Only the fields the
 * frontend actually needs — no internal backend detail.
 */

export interface ChatSessionResponse {
  id: string;
  created_at: string;
}

export interface ChatMessageResponse {
  id: string;
  role: "user" | "assistant";
  content: string;
  sequence_number: number;
  created_at: string;
}
