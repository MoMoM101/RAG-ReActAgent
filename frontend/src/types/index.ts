export interface Document {
  id: string;
  filename: string;
  file_size: number;
  file_type: string;
  status: "uploaded" | "waiting_for_ocr" | "parsing" | "chunking" | "embedding" | "indexing" | "ready" | "failed";
  chunk_count: number;
  error_message?: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name?: string;
  sources?: string;
}

export type SSEState = "idle" | "connecting" | "streaming" | "waiting_clarify" | "error";

export interface SSEEvent {
  event: string;
  data: unknown;
}
