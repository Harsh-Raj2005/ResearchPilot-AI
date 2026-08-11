/**
 * Documents page — Task 3C. The frontend document-management
 * experience on top of the existing backend Document CRUD +
 * processing endpoints. Deliberately narrow, matching the approved
 * scope: list, upload, download, delete, and an explicit "Process"
 * action. No detail page (DocumentResponse's four fields already fit
 * in a list row), no persisted "processed" status badge (the backend
 * has no such field — see types/document.ts), no PDF viewer, no
 * chat.
 *
 * Two deliberate policy decisions made during implementation, not
 * silently defaulted:
 * - A 401 from any document call logs the user out and redirects to
 *   /login (the frontend has no token-refresh mechanism, so a 401
 *   here can only mean an expired/invalid token — nothing else is
 *   worth doing with it).
 * - After any mutation (upload/delete/process), the list is
 *   refetched from scratch rather than updated optimistically —
 *   simplest option that stays correct, matching this project's
 *   existing "plain over clever" convention (e.g. list/detail's own
 *   plain skip/limit pagination instead of a pagination library).
 */
import { useCallback, useEffect, useState, type ChangeEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ROUTES } from "../constants/routes";
import { ApiError } from "../services/api";
import * as documentService from "../services/document";
import type { DocumentResponse } from "../types/document";

const PAGE_SIZE = 20;

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(isoString: string): string {
  return new Date(isoString).toLocaleString();
}

export default function DocumentsPage() {
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [skip, setSkip] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingList, setIsLoadingList] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [rowActionError, setRowActionError] = useState<Record<string, string>>({});
  const [rowActionMessage, setRowActionMessage] = useState<Record<string, string>>({});
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  /**
   * Central 401 handling: any authenticated document call that comes
   * back unauthorized logs the user out and sends them to /login,
   * rather than leaving the page stuck showing stale data behind a
   * now-invalid token. Returns true if the error was a 401 (and thus
   * already handled), so callers can skip their own error display.
   */
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

  const fetchDocuments = useCallback(
    async (currentSkip: number) => {
      if (!token) return;
      setIsLoadingList(true);
      setListError(null);
      try {
        const page = await documentService.listDocuments(token, {
          skip: currentSkip,
          limit: PAGE_SIZE,
        });
        setDocuments((previous) => (currentSkip === 0 ? page : [...previous, ...page]));
        setHasMore(page.length === PAGE_SIZE);
      } catch (err) {
        if (!handleAuthFailure(err)) {
          setListError(err instanceof Error ? err.message : "Failed to load documents");
        }
      } finally {
        setIsLoadingList(false);
      }
    },
    [token, handleAuthFailure]
  );

  useEffect(() => {
    void fetchDocuments(0);
    // Only on mount — pagination advances skip explicitly via handleLoadMore.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function refreshFromStart() {
    setSkip(0);
    void fetchDocuments(0);
  }

  function handleLoadMore() {
    const nextSkip = skip + PAGE_SIZE;
    setSkip(nextSkip);
    void fetchDocuments(nextSkip);
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // allow re-selecting the same file later
    if (!file || !token) return;

    setUploadError(null);
    setIsUploading(true);
    try {
      await documentService.uploadDocument(token, file);
      refreshFromStart();
    } catch (err) {
      if (!handleAuthFailure(err)) {
        setUploadError(err instanceof Error ? err.message : "Upload failed");
      }
    } finally {
      setIsUploading(false);
    }
  }

  async function handleDelete(documentId: string) {
    if (!token) return;
    if (!window.confirm("Delete this document? This cannot be undone.")) return;

    setDeletingId(documentId);
    setRowActionError((prev) => ({ ...prev, [documentId]: "" }));
    try {
      await documentService.deleteDocument(token, documentId);
      refreshFromStart();
    } catch (err) {
      if (!handleAuthFailure(err)) {
        setRowActionError((prev) => ({
          ...prev,
          [documentId]: err instanceof Error ? err.message : "Delete failed",
        }));
      }
    } finally {
      setDeletingId(null);
    }
  }

  async function handleProcess(documentId: string) {
    if (!token) return;

    setProcessingId(documentId);
    setRowActionError((prev) => ({ ...prev, [documentId]: "" }));
    setRowActionMessage((prev) => ({ ...prev, [documentId]: "" }));
    try {
      await documentService.processDocument(token, documentId);
       setRowActionMessage((prev) => ({ ...prev, [documentId]: "Processed successfully." }));
       refreshFromStart();
    } catch (err) {
      if (!handleAuthFailure(err)) {
        setRowActionError((prev) => ({
          ...prev,
          [documentId]: err instanceof Error ? err.message : "Processing failed",
        }));
      }
    } finally {
      setProcessingId(null);
    }
  }

  async function handleDownload(document: DocumentResponse) {
    if (!token) return;

    setDownloadingId(document.id);
    setRowActionError((prev) => ({ ...prev, [document.id]: "" }));
    try {
      const blob = await documentService.downloadDocument(token, document.id);
      const url = URL.createObjectURL(blob);
      const link = window.document.createElement("a");
      link.href = url;
      link.download = document.original_filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (!handleAuthFailure(err)) {
        setRowActionError((prev) => ({
          ...prev,
          [document.id]: err instanceof Error ? err.message : "Download failed",
        }));
      }
    } finally {
      setDownloadingId(null);
    }
  }

  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem", maxWidth: 720 }}>
      <h1>Documents</h1>

      <div style={{ marginBottom: "1.5rem" }}>
        <label htmlFor="upload">Upload a document (.pdf, .docx, .txt)</label>
        <br />
        <input
          id="upload"
          type="file"
          accept=".pdf,.docx,.txt"
          onChange={handleUpload}
          disabled={isUploading}
        />
        {isUploading && <p>Uploading...</p>}
        {uploadError && <p style={{ color: "crimson" }}>{uploadError}</p>}
      </div>

      {isLoadingList && documents.length === 0 && <p>Loading documents...</p>}
      {listError && <p style={{ color: "crimson" }}>{listError}</p>}
      {!isLoadingList && !listError && documents.length === 0 && (
        <p>No documents yet. Upload one to get started.</p>
      )}

      {documents.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
          <thead>
            <tr>
              <th style={{ borderBottom: "1px solid var(--border)", padding: "0.5rem 0" }}>
                Filename
              </th>
              <th style={{ borderBottom: "1px solid var(--border)", padding: "0.5rem 0" }}>
                Type
              </th>
              <th style={{ borderBottom: "1px solid var(--border)", padding: "0.5rem 0" }}>
                Size
              </th>
              <th style={{ borderBottom: "1px solid var(--border)", padding: "0.5rem 0" }}>
                Uploaded
              </th>
              <th style={{ borderBottom: "1px solid var(--border)", padding: "0.5rem 0" }}>
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {documents.map((document) => (
              <tr key={document.id}>
                <td style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border)" }}>
                  {document.original_filename}
                </td>
                <td style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border)" }}>
                  {document.content_type}
                </td>
                <td style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border)" }}>
                  {formatFileSize(document.file_size_bytes)}
                </td>
                <td style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border)" }}>
                  {formatDate(document.created_at)}
                </td>
                <td style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border)" }}>
                  <button
                    onClick={() => handleProcess(document.id)}
                    disabled={processingId === document.id}
                  >
                    {processingId === document.id ? "Processing..." : "Process"}
                  </button>{" "}
                  <button
                    onClick={() => handleDownload(document)}
                    disabled={downloadingId === document.id}
                  >
                    {downloadingId === document.id ? "Downloading..." : "Download"}
                  </button>{" "}
                  <button
                    onClick={() => handleDelete(document.id)}
                    disabled={deletingId === document.id}
                  >
                    {deletingId === document.id ? "Deleting..." : "Delete"}
                  </button>
                  {rowActionMessage[document.id] && (
                    <p style={{ margin: "0.25rem 0 0", color: "green" }}>
                      {rowActionMessage[document.id]}
                    </p>
                  )}
                  {rowActionError[document.id] && (
                    <p style={{ margin: "0.25rem 0 0", color: "crimson" }}>
                      {rowActionError[document.id]}
                    </p>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {hasMore && (
        <button onClick={handleLoadMore} disabled={isLoadingList} style={{ marginTop: "1rem" }}>
          {isLoadingList ? "Loading..." : "Load more"}
        </button>
      )}
    </main>
  );
}
