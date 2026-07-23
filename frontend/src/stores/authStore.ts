import { create } from "zustand";

const ACCESS_TOKEN_KEY = "rag_access_token";

export type AuthUser = {
  id: string;
  username: string;
  role: string;
};

type LoginResponse = {
  access_token: string;
  user: AuthUser;
};

type MeResponse = {
  user_id: string;
  username: string;
  role: string;
};

export type AuthState = {
  accessToken: string | null;
  user: AuthUser | null;
  authenticated: boolean;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  logout: () => Promise<void>;
  clearToken: () => void;
  checkAuth: () => Promise<void>;
};

function readAccessToken(): string | null {
  return sessionStorage.getItem(ACCESS_TOKEN_KEY);
}

function persistAccessToken(accessToken: string): void {
  sessionStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
}

function clearStoredToken(): void {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail || `认证失败 (${response.status})`;
  } catch {
    return `认证失败 (${response.status})`;
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: readAccessToken(),
  user: null,
  authenticated: false,
  loading: true,

  login: async (username: string, password: string) => {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) throw new Error(await errorDetail(response));

    const data = (await response.json()) as LoginResponse;
    persistAccessToken(data.access_token);
    set({
      accessToken: data.access_token,
      user: data.user,
      authenticated: true,
      loading: false,
    });
  },

  changePassword: async (currentPassword: string, newPassword: string) => {
    const response = await fetchWithAuth("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
    if (!response.ok) throw new Error(await errorDetail(response));

    const data = (await response.json()) as LoginResponse;
    persistAccessToken(data.access_token);
    set({
      accessToken: data.access_token,
      user: data.user,
      authenticated: true,
      loading: false,
    });
  },

  logout: async () => {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "include",
      });
    } finally {
      clearStoredToken();
      set({
        accessToken: null,
        user: null,
        authenticated: false,
        loading: false,
      });
    }
  },

  clearToken: () => {
    clearStoredToken();
    void fetch("/api/auth/logout", {
      method: "POST",
      credentials: "include",
    });
    set({
      accessToken: null,
      user: null,
      authenticated: false,
      loading: false,
    });
  },

  checkAuth: async () => {
    set({ loading: true });
    let accessToken = readAccessToken();
    if (!accessToken) accessToken = await refreshAccessToken();
    if (!accessToken) {
      set({ loading: false, authenticated: false, user: null });
      return;
    }

    let response = await fetch("/api/auth/me", {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (response.status === 401) {
      accessToken = await refreshAccessToken();
      if (accessToken) {
        response = await fetch("/api/auth/me", {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
      }
    }

    if (!response.ok) {
      clearStoredToken();
      set({
        accessToken: null,
        user: null,
        authenticated: false,
        loading: false,
      });
      return;
    }

    const me = (await response.json()) as MeResponse;
    set({
      accessToken,
      user: {
        id: me.user_id,
        username: me.username,
        role: me.role,
      },
      authenticated: true,
      loading: false,
    });
  },
}));

let refreshPromise: Promise<string | null> | null = null;

export function getAuthToken(): string | null {
  return readAccessToken();
}

export function authHeaders(extra: HeadersInit = {}): HeadersInit {
  const headers = new Headers(extra);
  const token = readAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return Object.fromEntries(headers.entries());
}

export async function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      const response = await fetch("/api/auth/refresh", {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) return null;

      const data = (await response.json()) as { access_token: string };
      persistAccessToken(data.access_token);
      useAuthStore.setState({
        accessToken: data.access_token,
      });
      return data.access_token;
    } catch {
      return null;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

export function requireAuthentication(): void {
  clearStoredToken();
  useAuthStore.setState({
    accessToken: null,
    user: null,
    authenticated: false,
    loading: false,
  });
  window.dispatchEvent(new CustomEvent("auth:required"));
}

export async function fetchWithAuth(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  let response = await fetch(input, {
    ...init,
    credentials: init.credentials ?? "include",
    headers: authHeaders(init.headers),
  });
  if (response.status !== 401) return response;

  const token = await refreshAccessToken();
  if (!token) {
    requireAuthentication();
    return response;
  }

  response = await fetch(input, {
    ...init,
    credentials: init.credentials ?? "include",
    headers: authHeaders(init.headers),
  });
  if (response.status === 401) requireAuthentication();
  return response;
}
