"use client";

import type { AuthSession, TokenResponse, User } from "@/lib/types";

const STORAGE_KEY = "inference-api.auth.v1";
let memorySession: AuthSession | null = null;

function isBrowser() {
  return typeof window !== "undefined";
}

function parseSession(value: string | null): AuthSession | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as AuthSession;
    if (!parsed.accessToken || !parsed.refreshToken || !parsed.user) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function tokenResponseToSession(response: TokenResponse): AuthSession {
  return {
    accessToken: response.access_token,
    refreshToken: response.refresh_token,
    expiresAt: Date.now() + response.expires_in * 1000,
    user: response.user
  };
}

export const authStore = {
  read(): AuthSession | null {
    if (memorySession) return memorySession;
    if (!isBrowser()) return null;
    memorySession = parseSession(window.localStorage.getItem(STORAGE_KEY));
    return memorySession;
  },

  write(session: AuthSession) {
    memorySession = session;
    if (isBrowser()) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
      window.dispatchEvent(new Event("inference-auth-changed"));
    }
  },

  updateUser(user: User) {
    const current = this.read();
    if (!current) return;
    this.write({ ...current, user });
  },

  clear() {
    memorySession = null;
    if (isBrowser()) {
      window.localStorage.removeItem(STORAGE_KEY);
      window.dispatchEvent(new Event("inference-auth-changed"));
    }
  },

  accessToken() {
    return this.read()?.accessToken ?? null;
  },

  refreshToken() {
    return this.read()?.refreshToken ?? null;
  }
};

export { STORAGE_KEY as AUTH_STORAGE_KEY };
