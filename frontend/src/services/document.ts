/**
 * Document API calls, isolated from UI components — same convention
 * as services/auth.ts. Nothing here knows about React; every
 * function takes the bearer token as an explicit argument rather
 * than reading it from context.
 */
import { deleteAuth, downloadAuth, getAuth, postAuth, uploadAuth } from "./api";
import type { DocumentResponse } from "../types/document";

const DOCUMENTS_PATH = "/api/v1/documents";

export function listDocuments(
  token: string,
  { skip = 0, limit = 20 }: { skip?: number; limit?: number } = {}
): Promise<DocumentResponse[]> {
  return getAuth<DocumentResponse[]>(
    `${DOCUMENTS_PATH}?skip=${skip}&limit=${limit}`,
    token
  );
}

export function uploadDocument(token: string, file: File): Promise<DocumentResponse> {
  return uploadAuth<DocumentResponse>(`${DOCUMENTS_PATH}/upload`, token, file);
}

export function deleteDocument(token: string, documentId: string): Promise<void> {
  return deleteAuth(`${DOCUMENTS_PATH}/${documentId}`, token);
}

/**
 * Triggers text extraction for an already-uploaded document — the
 * explicit POST /documents/{id}/process operation (Checkpoint 5).
 * Synchronous: the promise resolves only once parsing has actually
 * completed server-side. Safe to call again on an already-processed
 * document (reprocessing, via the backend's existing upsert
 * behavior) — this function has no notion of "already processed"
 * and doesn't need one.
 */
export function processDocument(token: string, documentId: string): Promise<DocumentResponse> {
  return postAuth<DocumentResponse>(`${DOCUMENTS_PATH}/${documentId}/process`, token);
}

/** Returns the raw file bytes for a document owned by the caller. */
export function downloadDocument(token: string, documentId: string): Promise<Blob> {
  return downloadAuth(`${DOCUMENTS_PATH}/${documentId}/file`, token);
}
