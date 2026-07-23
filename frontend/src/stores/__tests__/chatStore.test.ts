import { beforeEach, describe, expect, it, vi } from "vitest";

const chatApi = vi.hoisted(() => ({ sendMessage: vi.fn() }));

vi.mock("../../api/chat", () => chatApi);
vi.mock("../../api/conversations", () => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  deleteAllConversations: vi.fn(),
  getMessages: vi.fn(),
  renameConversation: vi.fn(),
}));

import type { SSEEvent } from "../../types";
import { useChatStore } from "../chatStore";

describe("chatStore empty answer fallback", () => {
  let onEvent: ((event: SSEEvent) => void) | undefined;
  let onError: ((error: Error) => void) | undefined;
  let onDone: (() => void) | undefined;

  beforeEach(() => {
    vi.clearAllMocks();
    onEvent = undefined;
    onError = undefined;
    onDone = undefined;
    chatApi.sendMessage.mockImplementation(
      (
        _text: string,
        _convId: string | null,
        event: typeof onEvent,
        error: typeof onError,
        done: typeof onDone,
      ) => {
        onEvent = event;
        onError = error;
        onDone = done;
        return new AbortController();
      },
    );
    useChatStore.setState({
      messages: [],
      conversations: [],
      currentConvId: "conv-1",
      sseState: "idle",
      error: null,
      abortController: null,
      loadingHistory: false,
    });
  });

  it("shows a visible fallback when done arrives after thoughts but no answer", async () => {
    await useChatStore.getState().send("skill 和 mcp 有什么区别");

    onEvent?.({ event: "thought", data: { delta: "正在整理资料" } });
    onEvent?.({ event: "sources", data: [] });
    onEvent?.({ event: "done", data: {} });

    const answer = useChatStore.getState().messages.at(-1);
    expect(answer?.content).toContain("未收到有效回答");
    expect(answer?.isStreaming).toBe(false);
  });

  it("removes tool-calling preamble before appending the final markdown answer", async () => {
    await useChatStore.getState().send("和 mcp 有什么区别");

    onEvent?.({ event: "answer_chunk", data: { delta: "先搜索一下相关资料。" } });
    onEvent?.({ event: "tool_call", data: { tool: "search_docs", args: {} } });
    onEvent?.({ event: "tool_result", data: { tool: "search_docs", success: true } });
    onEvent?.({ event: "answer_chunk", data: { delta: "### Skill\n\n- 工作流封装" } });

    const answer = useChatStore.getState().messages.at(-1);
    expect(answer?.content).toBe("### Skill\n\n- 工作流封装");
    expect(answer?.content).not.toContain("先搜索一下");
  });

  it("replaces streamed text with the backend-normalized final markdown", async () => {
    await useChatStore.getState().send("总结一下");

    onEvent?.({ event: "answer_chunk", data: { delta: "```markdown\n###总结\n```" } });
    onEvent?.({ event: "answer_replace", data: { content: "### 总结" } });

    expect(useChatStore.getState().messages.at(-1)?.content).toBe("### 总结");
  });

  it("shows an interruption fallback when the transport fails before content", async () => {
    await useChatStore.getState().send("问题");

    onError?.(new Error("network reset"));

    const answer = useChatStore.getState().messages.at(-1);
    expect(answer?.content).toContain("连接已中断");
    expect(useChatStore.getState().sseState).toBe("error");
  });

  it("shows a fallback when the stream closes without a done event", async () => {
    await useChatStore.getState().send("问题");

    onDone?.();

    expect(useChatStore.getState().messages.at(-1)?.content).toContain(
      "未收到有效回答",
    );
  });
});
