/**
 * Minimal fetch wrapper for talking to the backend.
 *
 * get() (Task 1) and post() (Task 2.3) share the same base URL and
 * the same error-extraction logic. Auth header injection is added
 * when the first protected endpoint needs it (a later task) — no
 * authenticated calls exist yet.
 */
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

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

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(await extractErrorMessage(response));
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
    throw new Error(await extractErrorMessage(response));
  }
  return response.json() as Promise<T>;
}
