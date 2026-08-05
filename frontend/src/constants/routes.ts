/**
 * Centralized route paths. Every <Route path> and every navigate()
 * call references this instead of a literal string, so adding or
 * renaming a route is a one-line change here.
 */
export const ROUTES = {
  home: "/",
  login: "/login",
  signup: "/signup",
} as const;
