import { Navigate, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { isAuthenticated } from "./token";

/**
 * Route guard: redirects to /login when no token is stored. Token *validity*
 * is enforced server-side (a stale token triggers 401 → the axios response
 * interceptor clears it and redirects). The guard only checks presence so
 * initial page load doesn't flash protected content.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation();
  if (!isAuthenticated()) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}
