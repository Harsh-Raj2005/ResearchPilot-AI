/**
 * Auth API calls, isolated from UI components. Pages/context call
 * these; nothing here knows about React.
 */
import { post } from "./api";
import type { LoginPayload, SignupPayload, TokenResponse, UserPublic } from "../types/auth";

export function signup(payload: SignupPayload): Promise<UserPublic> {
  return post<UserPublic>("/api/v1/auth/signup", payload);
}

export function login(payload: LoginPayload): Promise<TokenResponse> {
  return post<TokenResponse>("/api/v1/auth/login", payload);
}
