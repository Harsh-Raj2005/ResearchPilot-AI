/**
 * Route guard — the project's first real route protection (Task
 * 3C). Every prior route rendered regardless of auth state and just
 * varied its own content (see App.tsx's AuthPlaceholder); documents
 * are the first feature that genuinely must not render for a
 * logged-out visitor, since it would immediately fail against
 * protected endpoints.
 *
 * Redirects to /login rather than rendering an inline "please log
 * in" message, so a bookmarked/shared /documents link still lands
 * the visitor somewhere useful. `replace` avoids leaving the
 * protected route in browser history for the back button to return
 * to post-redirect.
 */
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ROUTES } from "../constants/routes";

export default function ProtectedRoute({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to={ROUTES.login} replace />;
  }

  return <>{children}</>;
}
