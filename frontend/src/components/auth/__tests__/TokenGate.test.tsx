import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TokenGate } from "../TokenGate";
import { useAuthStore } from "../../../stores/authStore";

// Mock fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// Mock sessionStorage with a Map
const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
  removeItem: (key: string) => storage.delete(key),
});

beforeEach(() => {
  storage.clear();
  mockFetch.mockReset();
  // Reset Zustand store to initial unauthenticated state
  act(() => {
    useAuthStore.setState({
      token: null,
      authenticated: false,
      loading: false,
    });
  });
});

describe("TokenGate", () => {
  it("shows login screen when not authenticated", () => {
    render(
      <TokenGate>
        <div>Protected Content</div>
      </TokenGate>
    );

    expect(screen.getByText("请输入管理令牌以继续")).toBeInTheDocument();
    expect(screen.queryByText("Protected Content")).not.toBeInTheDocument();
  });

  it("shows children when already authenticated", () => {
    act(() => {
      useAuthStore.setState({
        token: "valid-token",
        authenticated: true,
        loading: false,
      });
    });

    render(
      <TokenGate>
        <div>Protected Content</div>
      </TokenGate>
    );

    expect(screen.getByText("Protected Content")).toBeInTheDocument();
    expect(screen.queryByText("请输入管理令牌以继续")).not.toBeInTheDocument();
  });

  it("validates token on submit and grants access", async () => {
    const user = userEvent.setup();

    render(
      <TokenGate>
        <div>Protected Content</div>
      </TokenGate>
    );

    await user.type(screen.getByPlaceholderText("管理令牌"), "my-admin-token");

    mockFetch.mockResolvedValueOnce({ ok: true, status: 200 });

    await user.click(screen.getByRole("button", { name: "验证" }));

    await waitFor(() => {
      expect(screen.getByText("Protected Content")).toBeInTheDocument();
    });
    expect(sessionStorage.getItem("rag_admin_token")).toBe("my-admin-token");
  });

  it("shows error on wrong token", async () => {
    const user = userEvent.setup();

    render(
      <TokenGate>
        <div>Protected Content</div>
      </TokenGate>
    );

    await user.type(screen.getByPlaceholderText("管理令牌"), "wrong-token");

    mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });

    await user.click(screen.getByRole("button", { name: "验证" }));

    await waitFor(() => {
      expect(screen.getByText("令牌无效，请检查后重试")).toBeInTheDocument();
    });
  });

  it("shows error on connection failure", async () => {
    const user = userEvent.setup();

    render(
      <TokenGate>
        <div>Protected Content</div>
      </TokenGate>
    );

    await user.type(screen.getByPlaceholderText("管理令牌"), "token");

    mockFetch.mockRejectedValueOnce(new Error("Connection refused"));

    await user.click(screen.getByRole("button", { name: "验证" }));

    await waitFor(() => {
      expect(screen.getByText("无法连接后端服务，请确认服务已启动")).toBeInTheDocument();
    });
  });

  it("clears error when user types in input", async () => {
    const user = userEvent.setup();

    render(
      <TokenGate>
        <div>Protected Content</div>
      </TokenGate>
    );

    await user.type(screen.getByPlaceholderText("管理令牌"), "wrong");
    mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });
    await user.click(screen.getByRole("button", { name: "验证" }));

    await waitFor(() => {
      expect(screen.getByText("令牌无效，请检查后重试")).toBeInTheDocument();
    });

    // Type to clear error
    await user.type(screen.getByPlaceholderText("管理令牌"), "x");

    await waitFor(() => {
      expect(screen.queryByText("令牌无效，请检查后重试")).not.toBeInTheDocument();
    });
  });

  it("disables submit button when input is empty", () => {
    render(
      <TokenGate>
        <div>Protected Content</div>
      </TokenGate>
    );

    expect(screen.getByRole("button", { name: "验证" })).toBeDisabled();
  });

  it("uses password input type to hide token from screen", () => {
    render(
      <TokenGate>
        <div>Protected Content</div>
      </TokenGate>
    );

    expect(screen.getByPlaceholderText("管理令牌")).toHaveAttribute("type", "password");
  });

  it("shows loading state when auth check is in progress", async () => {
    // Set loading=true with a token so checkAuth won't immediately set loading=false
    // because it will try to call the health endpoint
    act(() => {
      useAuthStore.setState({
        token: "some-token",
        authenticated: false,
        loading: true,
      });
    });

    // Don't resolve the fetch - keep checkAuth pending
    mockFetch.mockImplementation(() => new Promise(() => {}));

    render(
      <TokenGate>
        <div>Protected Content</div>
      </TokenGate>
    );

    // The loading indicator should be visible while the health check is pending
    expect(screen.getByText("正在连接...")).toBeInTheDocument();
  });
});
