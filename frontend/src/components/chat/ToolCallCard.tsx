import { useState } from "react";
import type { AgentStep } from "../../types/chat";
import { ChevronDownIcon, ChevronUpIcon } from "../shared/Icons";

const toolLabels: Record<string, string> = {
  search_docs: "检索知识库",
  web_search: "联网搜索",
  recall_memory: "检索长期记忆",
  calculator: "计算",
  list_documents: "列出文档列表",
  get_document_info: "查询文档详情",
};

function optionalCount(value: unknown): number | null {
  const count = typeof value === "number" ? value : Number.NaN;
  return Number.isInteger(count) && count >= 0 ? count : null;
}

function resultDescription(tool: string, data: Record<string, unknown>): string {
  const count = optionalCount(data.result_count);
  const name = typeof data.result_name === "string" ? data.result_name : "";
  const value = data.result_value;

  switch (tool) {
    case "list_documents":
      return count === null ? "文档列表获取完成" : `文档列表：共 ${count} 个文档`;
    case "search_docs":
      return count === null ? "知识库检索完成" : `知识库检索：找到 ${count} 条结果`;
    case "web_search":
      return count === null ? "联网搜索完成" : `联网搜索：找到 ${count} 条结果`;
    case "recall_memory":
      return count === null ? "长期记忆检索完成" : `长期记忆：找到 ${count} 条记忆`;
    case "get_document_info":
      return name ? `文档详情：${name}` : "文档详情获取成功";
    case "calculator":
      return value !== undefined && value !== null ? `计算结果：${String(value)}` : "计算完成";
    default: {
      const operation = toolLabels[tool] || tool || "工具";
      return count === null ? `${operation}执行成功` : `${operation}：得到 ${count} 条结果`;
    }
  }
}

export function ToolCallCard({ step }: { step: AgentStep }) {
  const [expanded, setExpanded] = useState(false);
  const data = step.data as Record<string, unknown>;

  if (step.type === "tool_call") {
    const toolName = String(data.tool || "unknown");
    const label = toolLabels[toolName] || "执行工具";
    const argsObj = (data.args as Record<string, unknown>) || {};
    const hasArgs = Object.keys(argsObj).length > 0;
    return (
      <div className="tool-card" onClick={() => hasArgs && setExpanded(!expanded)} style={{ cursor: hasArgs ? "pointer" : "default" }}>
        <div className="tool-card-header">
          <span className="tool-card-name">{toolName}</span>
          <span>{label}</span>
          {hasArgs && (
            <span style={{ marginLeft: "auto", display: "flex", alignItems: "center" }}>
              {expanded ? <ChevronUpIcon size={12} /> : <ChevronDownIcon size={12} />}
            </span>
          )}
        </div>
        {expanded && hasArgs && (
          <div className="tool-card-body">
            {JSON.stringify(argsObj, null, 2)}
          </div>
        )}
      </div>
    );
  }

  if (step.type === "tool_result") {
    const success = data.success !== false;
    const tool = String(data.tool || "");
    const reranked = data.reranked === true;
    const operation = toolLabels[tool] || tool || "工具";

    return (
      <div className="tool-card" style={{
        borderColor: success ? "rgba(52,211,153,0.15)" : "rgba(248,113,113,0.15)",
        background: success ? "rgba(52,211,153,0.04)" : "rgba(248,113,113,0.04)",
      }}>
        <div className="tool-card-header" style={{ color: success ? "var(--success)" : "var(--danger)" }}>
          <span className={`status-dot ${success ? "ready" : "failed"}`} />
          {success ? resultDescription(tool, data) : `${operation}失败`}
          {success && reranked && (
            <span style={{ fontSize: 10, color: "var(--accent)", marginLeft: 6, fontWeight: 500 }}>⚡精排</span>
          )}
          {data.error != null && <span style={{ color: "var(--muted)", marginLeft: 6 }}>{String(data.error)}</span>}
        </div>
      </div>
    );
  }

  return null;
}
