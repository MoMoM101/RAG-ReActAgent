import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../../../stores/authStore";
import { TokenGate } from "../TokenGate";

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
  mockFetch.mockResolvedValue(new Response(null, { status: 401 }));
  act(() => useAuthStore.setState({
    accessToken: null,
    user: null,
    authenticated: false,
    loading: false,
  }));
});

describe("TokenGate", () => {
  it("shows an account login form when unauthenticated", async () => {
    render(<TokenGate><div>Protected Content</div></TokenGate>);
    expect(await screen.findByPlaceholderText("用户名")).toHaveValue("admin");
    expect(screen.getByPlaceholderText("密码")).toHaveAttribute("type", "password");
    expect(screen.queryByText("Protected Content")).not.toBeInTheDocument();
  });

  it("submits credentials and grants access", async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      if (input === "/api/auth/login") {
        return Promise.resolve(new Response(JSON.stringify({
          access_token: "access-1",
          user: { id: "user-1", username: "admin", role: "system_admin" },
        }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(null, { status: 401 }));
    });
    render(<TokenGate><div>Protected Content</div></TokenGate>);

    await user.type(await screen.findByPlaceholderText("密码"), "strong-password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(screen.getByText("Protected Content")).toBeInTheDocument());
    expect(storage.get("rag_access_token")).toBe("access-1");
    expect(storage.has("rag_refresh_token")).toBe(false);
  });

  it("shows the backend login error", async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      if (input === "/api/auth/login") {
        return Promise.resolve(new Response(
          JSON.stringify({ detail: "用户名或密码错误" }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ));
      }
      return Promise.resolve(new Response(null, { status: 401 }));
    });
    render(<TokenGate><div>Protected Content</div></TokenGate>);

    await user.type(await screen.findByPlaceholderText("密码"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("用户名或密码错误")).toBeInTheDocument();
  });

  it("offers optional password change beside login", async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      if (input === "/api/auth/login") {
        return Promise.resolve(new Response(JSON.stringify({
          access_token: "access-1",
          user: { id: "user-1", username: "admin", role: "system_admin" },
        }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      if (input === "/api/auth/change-password") {
        return Promise.resolve(new Response(JSON.stringify({
          access_token: "access-2",
          user: { id: "user-1", username: "admin", role: "system_admin" },
        }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(null, { status: 401 }));
    });

    render(<TokenGate><div>Protected Content</div></TokenGate>);
    await user.click(await screen.findByRole("button", { name: "修改密码" }));
    await user.type(screen.getByPlaceholderText("当前密码"), "admin123");
    await user.type(screen.getByPlaceholderText("新密码"), "1");
    await user.type(screen.getByPlaceholderText("再次输入新密码"), "1");
    await user.click(screen.getByRole("button", { name: "确认修改" }));

    expect(await screen.findByText("Protected Content")).toBeInTheDocument();
    expect(storage.get("rag_access_token")).toBe("access-2");
  });

  it("renders children for an authenticated session", async () => {
    storage.set("rag_access_token", "access-1");
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify({
      user_id: "user-1", username: "admin", role: "system_admin",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    act(() => useAuthStore.setState({
      accessToken: "access-1",
      authenticated: true,
      loading: false,
    }));
    render(<TokenGate><div>Protected Content</div></TokenGate>);
    expect(await screen.findByText("Protected Content")).toBeInTheDocument();
  });
});
