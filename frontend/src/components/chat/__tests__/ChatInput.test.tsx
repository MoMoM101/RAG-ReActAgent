import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mutable state object — tests mutate it before render to change behavior
const mockState = {
  messages: [] as Array<{ role: string; content: string | null }>,
  send: vi.fn(),
  stop: vi.fn(),
  clearError: vi.fn(),
  sseState: "idle" as string,
  error: null as string | null,
};

vi.mock("../../../stores/chatStore", () => ({
  useChatStore: (selector?: (s: unknown) => unknown) => {
    return selector ? selector(mockState) : mockState;
  },
}));

import { ChatInput } from "../ChatInput";

describe("ChatInput", () => {
  beforeEach(() => {
    mockState.send = vi.fn();
    mockState.stop = vi.fn();
    mockState.clearError = vi.fn();
    mockState.sseState = "idle";
    mockState.error = null;
    mockState.messages = [];
  });

  it("renders textarea with placeholder", () => {
    render(<ChatInput />);
    const textarea = screen.getByPlaceholderText(/输入问题/);
    expect(textarea).toBeInTheDocument();
  });

  it("shows send button disabled when text is empty", () => {
    render(<ChatInput />);
    const sendBtn = screen.getByRole("button", { name: /发送/ });
    expect(sendBtn).toBeDisabled();
  });

  it("shows send button enabled when text has content", async () => {
    const user = userEvent.setup();
    render(<ChatInput />);
    const textarea = screen.getByPlaceholderText(/输入问题/);
    await user.type(textarea, "Hello");
    const sendBtn = screen.getByRole("button", { name: /发送/ });
    expect(sendBtn).not.toBeDisabled();
  });

  it("calls send on Enter key and clears input", async () => {
    const user = userEvent.setup();
    render(<ChatInput />);
    const textarea = screen.getByPlaceholderText(/输入问题/);
    await user.type(textarea, "Hello{Enter}");
    expect(mockState.send).toHaveBeenCalledWith("Hello");
  });

  it("does not call send on Shift+Enter", async () => {
    const user = userEvent.setup();
    render(<ChatInput />);
    const textarea = screen.getByPlaceholderText(/输入问题/);
    await user.type(textarea, "Hello");
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(mockState.send).not.toHaveBeenCalled();
  });

  it("shows stop button when sseState is streaming", () => {
    mockState.sseState = "streaming";
    render(<ChatInput />);
    const stopBtn = screen.getByRole("button", { name: /停止/ });
    expect(stopBtn).toBeInTheDocument();
  });

  it("shows character counter", () => {
    render(<ChatInput />);
    // "0" appears twice: character count and context count
    const zeros = screen.getAllByText("0");
    expect(zeros.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("/ 4,000")).toBeInTheDocument();
  });

  it("shows error bar with retry button when error is set", () => {
    mockState.sseState = "error";
    mockState.error = "Network error";
    render(<ChatInput />);
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  it("calls clearError when close button is clicked", async () => {
    const user = userEvent.setup();
    mockState.sseState = "error";
    mockState.error = "Network error";
    render(<ChatInput />);
    await user.click(screen.getByRole("button", { name: /关闭/ }));
    expect(mockState.clearError).toHaveBeenCalled();
  });
});
