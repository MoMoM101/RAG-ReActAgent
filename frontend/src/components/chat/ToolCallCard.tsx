import { useState } from "react";
import type { AgentStep } from "../../types/chat";
import { ChevronDownIcon, ChevronUpIcon } from "../shared/Icons";

const toolLabels: Record<string, string> = {
  search_docs: "检索知识库",
  calculator: "计算",
  list_documents: "列出文档列表",
  get_document_info: "查询文档详情",
};

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
    const count = Number(data.result_count ?? 0);
    const tool = String(data.tool || "");
    const reranked = data.reranked === true;

    let resultLabel: string;
    if (tool === "list_documents") {
      resultLabel = `共 ${count} 个文档`;
    } else if (tool === "get_document_info") {
      resultLabel = success ? "查询成功" : "未找到";
    } else if (tool === "calculator") {
      resultLabel = success ? "计算完成" : "计算出错";
    } else {
      resultLabel = `检索到 ${count} 条结果`;
    }

    return (
      <div className="tool-card" style={{
        borderColor: success ? "rgba(52,211,153,0.15)" : "rgba(248,113,113,0.15)",
        background: success ? "rgba(52,211,153,0.04)" : "rgba(248,113,113,0.04)",
      }}>
        <div className="tool-card-header" style={{ color: success ? "var(--success)" : "var(--danger)" }}>
          <span className={`status-dot ${success ? "ready" : "failed"}`} />
          {success ? resultLabel : "调用失败"}
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
