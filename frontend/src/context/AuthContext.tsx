/**
 * Auth context definition + provider.
 *
 * This file defines the context and the component that provides it.
 * Consuming it is deliberately a separate file (see hooks/useAuth.ts)
 * so the context's shape and its consumption aren't coupled in one
 * file — the more conventional split, and easier to test in isolation
 * later.
 */
import { createContext, useState, type ReactNode } from "react";
import * as authService from "../services/auth";
import type { LoginPayload, SignupPayload } from "../types/auth";

const TOKEN_STORAGE_KEY = "researchpilot_access_token";
const EMAIL_STORAGE_KEY = "researchpilot_user_email";

export interface AuthContextValue {
  token: string | null;
  email: string | null;
  isAuthenticated: boolean;
  login: (payload: LoginPayload) => Promise<void>;
  signup: (payload: SignupPayload) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(TOKEN_STORAGE_KEY)
  );
  const [email, setEmail] = useState<string | null>(() =>
    localStorage.getItem(EMAIL_STORAGE_KEY)
  );

  async function login(payload: LoginPayload): Promise<void> {
    const { access_token } = await authService.login(payload);
    localStorage.setItem(TOKEN_STORAGE_KEY, access_token);
    localStorage.setItem(EMAIL_STORAGE_KEY, payload.email);
    setToken(access_token);
    setEmail(payload.email);
  }

  async function signup(payload: SignupPayload): Promise<void> {
    await authService.signup(payload);
    // POST /auth/signup returns UserPublic, not a token (see
    // app/schemas/auth.py) — the backend only issues tokens via
    // /auth/login. To land the user in an authenticated state
    // immediately after signup (per the approved plan: "navigate to
    // / after successful signup"), log in with the same credentials
    // right after. This reuses the existing login endpoint rather
    // than adding new backend behavior.
    await login({ email: payload.email, password: payload.password });
  }

  function logout(): void {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem(EMAIL_STORAGE_KEY);
    setToken(null);
    setEmail(null);
  }

  const value: AuthContextValue = {
    token,
    email,
    isAuthenticated: token !== null,
    login,
    signup,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
