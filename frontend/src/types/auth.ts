/**
 * Types mirroring the backend's app/schemas/auth.py. Kept in sync by
 * hand for now — no shared codegen between backend and frontend at
 * this project's scale.
 */

export interface SignupPayload {
  email: string;
  username: string;
  password: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserPublic {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  created_at: string;
}
