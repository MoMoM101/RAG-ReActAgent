import { create } from "zustand";

const TOKEN_KEY = "rag_admin_token";

function readToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export type AuthState = {
  token: string | null;
  authenticated: boolean;
  loading: boolean;
  setToken: (token: string) => void;
  clearToken: () => void;
  checkAuth: () => Promise<void>;
};

export const useAuthStore = create<AuthState>((set, get) => ({
  token: readToken(),
  authenticated: false,
  loading: true,

  setToken: (token: string) => {
    sessionStorage.setItem(TOKEN_KEY, token);
    set({ token, authenticated: true });
  },

  clearToken: () => {
    sessionStorage.removeItem(TOKEN_KEY);
    set({ token: null, authenticated: false });
  },

  checkAuth: async () => {
    const token = get().token;
    if (!token) {
      set({ loading: false, authenticated: false });
      return;
    }
    try {
      const res = await fetch("/api/health", {
        headers: { "X-Admin-Token": token },
      });
      set({ authenticated: res.ok, loading: false });
      if (!res.ok) {
        sessionStorage.removeItem(TOKEN_KEY);
      }
    } catch {
      set({ loading: false });
    }
  },
}));

export function getAuthToken(): string | null {
  return readToken();
}

export function authHeaders(extra: HeadersInit = {}): HeadersInit {
  const token = readToken();
  const base: Record<string, string> = { ...(extra as Record<string, string>) };
  if (token) {
    base["X-Admin-Token"] = token;
  }
  return base;
}
