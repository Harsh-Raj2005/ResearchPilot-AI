/**
 * Minimal fetch wrapper for talking to the backend.
 *
 * get() (Task 1) and post() (Task 2.3) share the same base URL and
 * the same error-extraction logic. Task 3C adds the authenticated
 * variants (getAuth/postAuth/uploadAuth/deleteAuth/downloadAuth) —
 * the first protected endpoints this project calls from the
 * frontend. Auth header injection is deliberately done by having
 * callers pass a `token` argument explicitly, rather than this
 * module reaching into AuthContext itself — mirrors services/auth.ts's
 * existing "nothing here knows about React" boundary.
 */
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

/**
 * Thrown by every function in this module on a non-2xx response.
 * Extends Error so existing `err instanceof Error` checks (e.g. in
 * LoginPage/SignupPage) keep working unchanged; the extra `status`
 * field lets callers that need it (e.g. a 401 -> logout policy)
 * branch on the HTTP status without parsing the message string.
 */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * FastAPI error bodies are either { detail: string } (HTTPException)
 * or { detail: [{ msg: string, ... }, ...] } (Pydantic validation
 * errors, 422). This extracts a single readable message from either
 * shape, falling back to the HTTP status if the body doesn't parse.
 */
async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") {
      return body.detail;
    }
    if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      return body.detail[0].msg as string;
    }
  } catch {
    // Response body wasn't JSON — fall through to the generic message.
  }
  return `Request failed: ${response.status}`;
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

/** Authenticated GET — e.g. GET /documents, GET /documents/{id}. */
export async function getAuth<T>(path: string, token: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

/** Authenticated POST with no request body — e.g. POST /documents/{id}/process. */
export async function postAuth<T>(path: string, token: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

/**
 * Authenticated multipart upload — POST /documents/upload.
 * Deliberately does not set Content-Type: the browser sets the
 * multipart boundary itself when the body is a FormData instance;
 * setting it manually would omit the boundary and break the request.
 */
export async function uploadAuth<T>(path: string, token: string, file: File): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders(token),
    body: formData,
  });
  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

/** Authenticated DELETE — e.g. DELETE /documents/{id}. Backend returns 204, no body. */
export async function deleteAuth(path: string, token: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
}

/**
 * Authenticated binary download — GET /documents/{id}/file. Returns
 * the raw Blob; the caller already has the document's
 * original_filename from DocumentResponse, so this doesn't attempt
 * to parse Content-Disposition (which isn't readable cross-origin
 * without an additional backend CORS `expose_headers` change outside
 * this task's approved scope).
 */
export async function downloadAuth(path: string, token: string): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
  return response.blob();
}
