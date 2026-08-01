/**
 * Minimal fetch wrapper for talking to the backend.
 *
 * Task 1 scope: base URL + a generic get() used only by the health
 * check smoke test below. Auth header injection, POST/upload helpers,
 * and error typing are added in the tasks that actually need them
 * (auth, documents, chat) rather than speculatively here.
 */
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`GET ${path} failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}
