export interface AgentStep {
  type: "status" | "tool_call" | "tool_result" | "clarification" | "answer_chunk" | "thought" | "sources" | "error" | "done";
  data: Record<string, unknown>;
  timestamp: number;
}

export interface DisplayMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  thought?: string;
  steps: AgentStep[];
  sources?: Array<{ document_id: string; text: string }>;
  isStreaming: boolean;
  startTime?: number;
  duration?: number;
}
