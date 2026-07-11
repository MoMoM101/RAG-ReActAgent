import { describe, it, expect, vi, beforeEach } from "vitest";
import { useAuthStore, authHeaders, getAuthToken } from "../authStore";

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
  // Reset Zustand store to initial state
  useAuthStore.setState({
    token: null,
    authenticated: false,
    loading: true,
  });
});

describe("authStore", () => {
  describe("checkAuth", () => {
    it("sets loading false and unauthenticated when no token stored", async () => {
      await useAuthStore.getState().checkAuth();

      const state = useAuthStore.getState();
      expect(state.loading).toBe(false);
      expect(state.authenticated).toBe(false);
      expect(state.token).toBeNull();
    });

    it("validates stored token with health endpoint", async () => {
      storage.set("rag_admin_token", "my-token");
      useAuthStore.setState({ token: "my-token" });

      mockFetch.mockResolvedValueOnce({ ok: true, status: 200 });

      await useAuthStore.getState().checkAuth();

      const state = useAuthStore.getState();
      expect(state.authenticated).toBe(true);
      expect(state.loading).toBe(false);
    });

    it("clears invalid stored token on 401", async () => {
      storage.set("rag_admin_token", "expired-token");
      useAuthStore.setState({ token: "expired-token" });

      mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });

      await useAuthStore.getState().checkAuth();

      const state = useAuthStore.getState();
      expect(state.authenticated).toBe(false);
      expect(sessionStorage.getItem("rag_admin_token")).toBeNull();
    });

    it("sends X-Admin-Token header with health check", async () => {
      storage.set("rag_admin_token", "test-token");
      useAuthStore.setState({ token: "test-token" });

      mockFetch.mockResolvedValueOnce({ ok: true, status: 200 });

      await useAuthStore.getState().checkAuth();

      expect(mockFetch).toHaveBeenCalledWith("/api/health", {
        headers: { "X-Admin-Token": "test-token" },
      });
    });

    it("stays loading=false even on network error", async () => {
      storage.set("rag_admin_token", "test-token");
      useAuthStore.setState({ token: "test-token" });

      mockFetch.mockRejectedValueOnce(new Error("Network error"));

      await useAuthStore.getState().checkAuth();

      const state = useAuthStore.getState();
      expect(state.loading).toBe(false);
    });
  });

  describe("setToken", () => {
    it("stores token in sessionStorage and sets authenticated", () => {
      useAuthStore.getState().setToken("new-token");

      const state = useAuthStore.getState();
      expect(state.token).toBe("new-token");
      expect(state.authenticated).toBe(true);
      expect(storage.get("rag_admin_token")).toBe("new-token");
    });
  });

  describe("clearToken", () => {
    it("removes token from sessionStorage and resets state", () => {
      storage.set("rag_admin_token", "old-token");
      useAuthStore.setState({ token: "old-token", authenticated: true });

      useAuthStore.getState().clearToken();

      const state = useAuthStore.getState();
      expect(state.token).toBeNull();
      expect(state.authenticated).toBe(false);
      expect(sessionStorage.getItem("rag_admin_token")).toBeNull();
    });
  });
});

describe("authHeaders", () => {
  it("returns headers with X-Admin-Token when token exists", () => {
    storage.set("rag_admin_token", "header-token");

    const headers = authHeaders();
    expect(headers).toEqual({ "X-Admin-Token": "header-token" });
  });

  it("returns empty headers when no token", () => {
    const headers = authHeaders();
    expect(headers).toEqual({});
  });

  it("merges with extra headers", () => {
    storage.set("rag_admin_token", "header-token");

    const headers = authHeaders({ "Content-Type": "application/json" });
    expect(headers).toEqual({
      "X-Admin-Token": "header-token",
      "Content-Type": "application/json",
    });
  });
});

describe("getAuthToken", () => {
  it("returns token from sessionStorage", () => {
    storage.set("rag_admin_token", "get-token");
    expect(getAuthToken()).toBe("get-token");
  });

  it("returns null when no token stored", () => {
    expect(getAuthToken()).toBeNull();
  });
});
