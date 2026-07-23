import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolCallCard } from "../ToolCallCard";

describe("ToolCallCard result summaries", () => {
  it("shows the real list_documents count", () => {
    render(<ToolCallCard step={{
      type: "tool_result",
      data: { tool: "list_documents", success: true, result_count: 2 },
      timestamp: Date.now(),
    }} />);

    expect(screen.getByText("文档列表：共 2 个文档")).toBeInTheDocument();
  });

  it("does not invent zero when a count is unavailable", () => {
    render(<ToolCallCard step={{
      type: "tool_result",
      data: { tool: "list_documents", success: true, result_count: null },
      timestamp: Date.now(),
    }} />);

    expect(screen.getByText("文档列表获取完成")).toBeInTheDocument();
    expect(screen.queryByText(/0 个文档/)).not.toBeInTheDocument();
  });

  it("shows operation-specific values and names", () => {
    const { rerender } = render(<ToolCallCard step={{
      type: "tool_result",
      data: { tool: "calculator", success: true, result_value: 42 },
      timestamp: Date.now(),
    }} />);
    expect(screen.getByText("计算结果：42")).toBeInTheDocument();

    rerender(<ToolCallCard step={{
      type: "tool_result",
      data: { tool: "get_document_info", success: true, result_name: "guide.pdf" },
      timestamp: Date.now(),
    }} />);
    expect(screen.getByText("文档详情：guide.pdf")).toBeInTheDocument();
  });

  it("identifies the failed operation", () => {
    render(<ToolCallCard step={{
      type: "tool_result",
      data: { tool: "web_search", success: false, error: "timeout" },
      timestamp: Date.now(),
    }} />);

    expect(screen.getByText("联网搜索失败")).toBeInTheDocument();
    expect(screen.getByText("timeout")).toBeInTheDocument();
  });
});
