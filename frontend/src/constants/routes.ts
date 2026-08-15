/**
 * Centralized route paths. Every <Route path> and every navigate()
 * call references this instead of a literal string, so adding or
 * renaming a route is a one-line change here.
 */
export const ROUTES = {
  home: "/",
  login: "/login",
  signup: "/signup",
  documents: "/documents",
  chat: "/documents/:documentId/chat",
} as const;

/** Builds a concrete chat URL for a specific document — pairs with ROUTES.chat's pattern. */
export function chatPath(documentId: string): string {
  return `/documents/${documentId}/chat`;
}
