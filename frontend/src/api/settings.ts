import { apiGet, apiPut, apiPost } from "./client";
import { fetchWithAuth } from "../stores/authStore";

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

export interface OptionalModelStatus {
  status: "disabled" | "downloading" | "loading" | "ready" | "failed" | "missing_dependency";
  enabled: boolean;
  optional: boolean;
  cached: boolean | null;
  elapsed_seconds: number;
  notice_seconds: number;
  continuing_in_background: boolean;
  slow: boolean;
  message: string;
  last_error: string;
  manual_command: string;
}

export interface DependencyHealth {
  status: string;
  core_ready: boolean;
  optional_models: {
    ocr?: OptionalModelStatus;
    reranker?: OptionalModelStatus;
  };
}

export function getDependencyHealth(): Promise<DependencyHealth> {
  return apiGet("/api/health/dependencies");
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
  const controller = new AbortController();
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    controller.abort();
    onDone();
  };

  void (async () => {
    try {
      const response = await fetchWithAuth("/api/settings/rebuild-progress", {
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!finished) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const dataLine = block.split("\n").find((line) => line.startsWith("data:"));
          if (dataLine) {
            const data = JSON.parse(dataLine.slice(5).trim()) as RebuildProgressEvent;
            onEvent(data);
            if (["completed", "failed", "timeout"].includes(data.status)) {
              finish();
              return;
            }
          }
          boundary = buffer.indexOf("\n\n");
        }
        if (done) break;
      }
    } catch (error) {
      if (!controller.signal.aborted) void error;
    }
    finish();
  })();

  return finish;
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
