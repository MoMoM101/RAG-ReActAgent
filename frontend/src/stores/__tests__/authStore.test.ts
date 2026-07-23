import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  authHeaders,
  fetchWithAuth,
  getAuthToken,
  useAuthStore,
} from "../authStore";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
  removeItem: (key: string) => storage.delete(key),
});

beforeEach(() => {
  storage.clear();
  mockFetch.mockReset();
  useAuthStore.setState({
    accessToken: null,
    user: null,
    authenticated: false,
    loading: true,
  });
});

describe("authStore", () => {
  it("logs in and stores only the access token", async () => {
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify({
      access_token: "access-1",
      user: { id: "user-1", username: "admin", role: "system_admin" },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await useAuthStore.getState().login("admin", "strong-password");

    expect(mockFetch).toHaveBeenCalledWith("/api/auth/login", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ username: "admin", password: "strong-password" }),
    }));
    expect(storage.get("rag_access_token")).toBe("access-1");
    expect(storage.has("rag_refresh_token")).toBe(false);
    expect(useAuthStore.getState().authenticated).toBe(true);
  });

  it("validates an existing session against the protected me endpoint", async () => {
    storage.set("rag_access_token", "access-1");
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify({
      user_id: "user-1", username: "admin", role: "system_admin",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await useAuthStore.getState().checkAuth();

    expect(mockFetch).toHaveBeenCalledWith("/api/auth/me", {
      headers: { Authorization: "Bearer access-1" },
    });
    expect(useAuthStore.getState().authenticated).toBe(true);
  });

  it("restores login from the HttpOnly cookie after reopening the page", async () => {
    mockFetch
      .mockResolvedValueOnce(new Response(JSON.stringify({
        access_token: "access-restored",
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        user_id: "user-1", username: "admin", role: "system_admin",
      }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await useAuthStore.getState().checkAuth();

    expect(mockFetch).toHaveBeenNthCalledWith(1, "/api/auth/refresh", {
      method: "POST",
      credentials: "include",
    });
    expect(storage.get("rag_access_token")).toBe("access-restored");
    expect(useAuthStore.getState().authenticated).toBe(true);
  });

  it("refreshes an expired access token through the HttpOnly cookie", async () => {
    storage.set("rag_access_token", "expired");
    mockFetch
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        access_token: "access-2",
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        user_id: "user-1", username: "admin", role: "system_admin",
      }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await useAuthStore.getState().checkAuth();

    expect(storage.get("rag_access_token")).toBe("access-2");
    expect(mockFetch).toHaveBeenNthCalledWith(2, "/api/auth/refresh", {
      method: "POST",
      credentials: "include",
    });
    expect(useAuthStore.getState().authenticated).toBe(true);
  });

  it("clears the access token and requests cookie logout", () => {
    storage.set("rag_access_token", "access-1");
    useAuthStore.getState().clearToken();
    expect(storage.size).toBe(0);
    expect(mockFetch).toHaveBeenCalledWith("/api/auth/logout", {
      method: "POST",
      credentials: "include",
    });
    expect(useAuthStore.getState().authenticated).toBe(false);
  });

  it("waits for server logout before clearing local authentication", async () => {
    storage.set("rag_access_token", "access-1");
    useAuthStore.setState({ accessToken: "access-1", authenticated: true });
    mockFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await useAuthStore.getState().logout();

    expect(mockFetch).toHaveBeenCalledWith("/api/auth/logout", {
      method: "POST",
      credentials: "include",
    });
    expect(storage.size).toBe(0);
    expect(useAuthStore.getState().authenticated).toBe(false);
  });
});

describe("authenticated request helpers", () => {
  it("builds a bearer header", () => {
    storage.set("rag_access_token", "access-1");
    expect(authHeaders({ "Content-Type": "application/json" })).toEqual({
      authorization: "Bearer access-1",
      "content-type": "application/json",
    });
    expect(getAuthToken()).toBe("access-1");
  });

  it("retries once after refreshing a rejected request", async () => {
    storage.set("rag_access_token", "expired");
    mockFetch
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        access_token: "access-2",
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response("ok", { status: 200 }));

    const response = await fetchWithAuth("/api/documents");

    expect(response.status).toBe(200);
    expect(mockFetch).toHaveBeenLastCalledWith("/api/documents", {
      credentials: "include",
      headers: { authorization: "Bearer access-2" },
    });
  });
});
