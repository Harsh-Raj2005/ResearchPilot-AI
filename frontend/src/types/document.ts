/**
 * Types mirroring the backend's app/schemas/document.py. Kept in
 * sync by hand, same convention as types/auth.ts.
 *
 * Deliberately matches DocumentResponse exactly — no status,
 * extracted-text, or storage-path fields exist here because the
 * backend doesn't return them (see app/schemas/document.py's own
 * docstring: stored_filename/storage_path are intentionally never
 * exposed, and no processing-state field exists at all).
 */

export interface DocumentResponse {
  id: string;
  original_filename: string;
  content_type: string;
  file_size_bytes: number;
  created_at: string;
}
