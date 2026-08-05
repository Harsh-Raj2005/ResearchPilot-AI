import { useContext } from "react";
import { AuthContext } from "../context/AuthContext";

/**
 * Consumes AuthContext. Throws if used outside AuthProvider, so a
 * missing provider fails loudly at the call site instead of silently
 * returning undefined.
 */
export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
