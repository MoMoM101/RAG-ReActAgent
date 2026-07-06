import { apiGet, apiPut, apiPost } from "./client";

export interface LLMSettings {
  provider: string;
  model: string;
  api_key: string;
  base_url: string;
}

export interface EmbeddingSettings {
  provider: string;
  model: string;
  api_key: string;
  base_url: string;
}

export interface SettingsResponse {
  llm: LLMSettings;
  embedding: EmbeddingSettings;
  web_search_enabled: boolean;
  rerank_enabled: boolean;
  retrieval_top_k: number;
  web_search_max_results: number;
  chunk_size: number;
  chunk_overlap: number;
}

export function getSettings(): Promise<SettingsResponse> {
  return apiGet("/api/settings");
}

export function updateSettings(data: SettingsResponse): Promise<{ status: string; dimension?: DimensionCheckResult }> {
  return apiPut("/api/settings", data);
}

export interface TestConnectionResult {
  ok: boolean;
  latency_ms: number;
  detail: string;
}

export function testConnection(config: {
  provider: string;
  model: string;
  api_key: string;
  base_url: string;
  kind: "llm" | "embedding";
}): Promise<TestConnectionResult> {
  return apiPost("/api/settings/test-connection", config);
}

export interface DimensionCheckResult {
  ok: boolean;
  current_model_dim?: number;
  rag_chunks_dim?: number | null;
  profile_dim?: number | null;
  mismatch?: boolean;
  document_count?: number;
  error?: string;
}

export function checkDimension(): Promise<DimensionCheckResult> {
  return apiPost("/api/settings/dimension-check");
}

export interface RebuildResult {
  status: string;  // "started" | "rejected"
  reason?: string;
}

export interface RebuildProgressEvent {
  status: string;  // "preflight" | "rebuilding" | "switching" | "completed" | "failed" | "timeout"
  message?: string;
  current?: number;
  total?: number;
  filename?: string;
  chunk_count?: number;
  actual_chunk_size?: number;
  actual_chunk_dim?: number;
  error?: string;
  failed_count?: number;
}

export function rebuildCollections(): Promise<RebuildResult> {
  return apiPost("/api/settings/rebuild-collections");
}

export function getRebuildStatus(): Promise<RebuildProgressEvent> {
  return apiGet("/api/settings/rebuild-status");
}

export function subscribeRebuildProgress(
  onEvent: (event: RebuildProgressEvent) => void,
  onDone: () => void,
): () => void {
  const es = new EventSource("/api/settings/rebuild-progress");

  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data) as RebuildProgressEvent;
      onEvent(data);
      if (data.status === "completed" || data.status === "failed" || data.status === "timeout") {
        es.close();
        onDone();
      }
    } catch {
      // ignore parse errors
    }
  };

  es.onerror = () => {
    es.close();
    onDone();
  };

  return () => es.close();
}

export interface ClearAllResult {
  status: string;
  deleted: {
    documents: number;
    chunks: number;
    memories: number;
    conversations: number;
    messages: number;
  };
}

export function clearAllData(): Promise<ClearAllResult> {
  return apiPost("/api/settings/clear-all-data");
}
