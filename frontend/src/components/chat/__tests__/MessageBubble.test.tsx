import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { MessageBubble } from "../MessageBubble";
import type { DisplayMessage } from "../../../types/chat";

function makeMsg(overrides: Partial<DisplayMessage> = {}): DisplayMessage {
  return {
    id: "msg-1",
    role: "user",
    content: "",
    steps: [],
    isStreaming: false,
    ...overrides,
  };
}

describe("MessageBubble", () => {
  it("renders user message with correct role label", () => {
    const msg = makeMsg({ role: "user", content: "Hello world" });
    render(<MessageBubble message={msg} />);
    expect(screen.getByText("你")).toBeInTheDocument();
    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });

  it("renders user message with U avatar", () => {
    const msg = makeMsg({ role: "user", content: "test" });
    render(<MessageBubble message={msg} />);
    expect(screen.getByText("U")).toBeInTheDocument();
  });

  it("renders assistant message with AI avatar", () => {
    const msg = makeMsg({ role: "assistant", content: "I can help" });
    render(<MessageBubble message={msg} />);
    expect(screen.getByText("AI")).toBeInTheDocument();
    expect(screen.getByText("RAG Agent")).toBeInTheDocument();
  });

  it("renders markdown content", () => {
    const msg = makeMsg({ role: "assistant", content: "**bold text**" });
    render(<MessageBubble message={msg} />);
    const bold = screen.getByText("bold text");
    expect(bold.tagName).toBe("STRONG");
  });

  it("renders headings and lists as structured markdown", () => {
    const msg = makeMsg({
      role: "assistant",
      content: "### Skill\n\n- 可复用工作流\n- 按需加载",
    });
    const { container } = render(<MessageBubble message={msg} />);

    expect(screen.getByRole("heading", { level: 3, name: "Skill" })).toBeInTheDocument();
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("wraps GFM tables for horizontal scrolling", () => {
    const msg = makeMsg({
      role: "assistant",
      content: "| 项目 | 说明 |\n|---|---|\n| MCP | 外部连接 |",
    });
    const { container } = render(<MessageBubble message={msg} />);

    expect(container.querySelector(".markdown-table-wrap > table")).toBeInTheDocument();
  });

  it("opens markdown links safely in a new tab", () => {
    const msg = makeMsg({ role: "assistant", content: "[文档](https://example.com/docs)" });
    render(<MessageBubble message={msg} />);

    const link = screen.getByRole("link", { name: "文档" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("shows feedback buttons when not streaming and has content", () => {
    const msg = makeMsg({ role: "assistant", content: "response", isStreaming: false });
    render(<MessageBubble message={msg} />);
    expect(screen.getByTitle("有帮助")).toBeInTheDocument();
    expect(screen.getByTitle("没帮助")).toBeInTheDocument();
  });

  it("hides feedback buttons when streaming", () => {
    const msg = makeMsg({ role: "assistant", content: "partial", isStreaming: true });
    render(<MessageBubble message={msg} />);
    expect(screen.queryByTitle("有帮助")).not.toBeInTheDocument();
  });

  it("shows streaming cursor indicator when streaming", () => {
    const msg = makeMsg({ role: "assistant", content: "streaming...", isStreaming: true });
    const { container } = render(<MessageBubble message={msg} />);
    // Streaming cursor is a styled span, check it's rendered
    const cursorSpan = container.querySelector(
      'span[style*="inline-block"][style*="width: 8"]'
    );
    expect(cursorSpan).toBeTruthy();
  });

  it("shows duration when message has completed", () => {
    const msg = makeMsg({ role: "assistant", content: "done", duration: 1200 });
    render(<MessageBubble message={msg} />);
    expect(screen.getByText(/耗时/)).toBeInTheDocument();
  });

  it("hides duration when streaming", () => {
    const msg = makeMsg({ role: "assistant", content: "partial", isStreaming: true, duration: 500 });
    render(<MessageBubble message={msg} />);
    expect(screen.queryByText(/耗时/)).not.toBeInTheDocument();
  });

  it("renders sources when present", () => {
    const msg = makeMsg({
      role: "assistant",
      content: "answer",
      sources: [{ document_id: "d1", text: "source text" }],
    });
    render(<MessageBubble message={msg} />);
    // SourceCard renders text as title attribute on chip button
    expect(screen.getByTitle("source text")).toBeInTheDocument();
  });

  it("hides misleading warning when content is supported but citation markers are missing", () => {
    const msg = makeMsg({
      role: "assistant",
      content: "supported answer",
      verification: {
        status: "partial", claim_count: 1, supported_claims: 1,
        faithfulness: 1, citation_precision: 0, citation_recall: 0,
        sources_used: 1, unsupported_claims: [], display_status: "hidden",
      },
    });
    render(<MessageBubble message={msg} />);
    expect(screen.queryByText(/来源支持不完整|缺少来源支持/)).not.toBeInTheDocument();
  });

  it("shows warning only when factual claims are unsupported", () => {
    const msg = makeMsg({
      role: "assistant",
      content: "unsupported answer",
      verification: {
        status: "partial", claim_count: 2, supported_claims: 1,
        faithfulness: 0.5, citation_precision: 0.5, citation_recall: 1,
        sources_used: 1, unsupported_claims: ["unsupported claim"], display_status: "warning",
      },
    });
    render(<MessageBubble message={msg} />);
    expect(screen.getByText(/部分事实缺少来源支持/)).toBeInTheDocument();
  });

  it("renders code block with language label", () => {
    const msg = makeMsg({
      role: "assistant",
      content: '```python\nprint("hello")\n```',
    });
    render(<MessageBubble message={msg} />);
    expect(screen.getByText("python")).toBeInTheDocument();
    expect(screen.getByText('print("hello")')).toBeInTheDocument();
  });

  it("renders fenced code without a language as a code block", () => {
    const msg = makeMsg({
      role: "assistant",
      content: "```\nplain code\n```",
    });
    const { container } = render(<MessageBubble message={msg} />);

    expect(screen.getByText("code")).toBeInTheDocument();
    expect(container.querySelector(".code-block pre code")?.textContent).toBe("plain code\n");
  });
});
