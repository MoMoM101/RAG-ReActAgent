export interface AgentStep {
  type: "status" | "tool_call" | "tool_result" | "clarification" | "answer_chunk" | "thought" | "sources" | "verification" | "error" | "done";
  data: Record<string, unknown>;
  timestamp: number;
}

export interface SourceReference {
  citation_id?: string;
  chunk_id?: string;
  document_id: string;
  document_key?: string;
  section_key?: string;
  filename?: string;
  text: string;
  score?: number;
  rank?: number;
}

export interface GroundingVerification {
  status: "verified" | "partial" | "unverified" | "no_sources";
  claim_count: number;
  supported_claims: number;
  faithfulness: number;
  citation_precision: number;
  citation_recall: number;
  sources_used: number;
  unsupported_claims: string[];
  display_status?: "verified" | "warning" | "hidden";
  citation_status?: "complete" | "partial" | "missing";
}

export interface DisplayMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  thought?: string;
  steps: AgentStep[];
  sources?: SourceReference[];
  verification?: GroundingVerification;
  isStreaming: boolean;
  startTime?: number;
  duration?: number;
}
