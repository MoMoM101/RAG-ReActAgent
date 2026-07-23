import { apiDelete, apiGet, apiPost, apiUpload } from "./client";
import type { Document, DocumentChunks } from "../types/document";
import { fetchWithAuth } from "../stores/authStore";

export function listDocuments(): Promise<Document[]> {
  return apiGet("/api/documents");
}

export function uploadDocument(
  file: File,
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
): Promise<Document> {
  const form = new FormData();
  form.append("file", file);
  return apiUpload("/api/documents/upload", form, onProgress, signal);
}

export interface BatchUploadItem {
  filename: string;
  success: boolean;
  document?: Document;
  status_code?: number;
  error?: string;
}

export interface BatchUploadResponse {
  items: BatchUploadItem[];
  total: number;
  succeeded: number;
  failed: number;
}

export function uploadDocuments(
  files: File[],
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
): Promise<BatchUploadResponse> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  return apiUpload("/api/documents/upload-batch", form, onProgress, signal);
}

export interface UploadConfig {
  max_upload_mb: number;
  hard_limit_mb: number;
  batch_max_files?: number;
  batch_max_total_mb?: number;
  allowed_extensions: string[];
}

export function getUploadConfig(): Promise<UploadConfig> {
  return apiGet("/api/documents/upload-config");
}

export function deleteDocument(id: string): Promise<void> {
  return apiDelete(`/api/documents/${id}`);
}

export function clearAllDocuments(): Promise<{ status: string; count: number }> {
  return apiDelete("/api/documents/clear-all");
}

export function getDocumentChunks(id: string): Promise<DocumentChunks> {
  return apiGet(`/api/documents/${id}/chunks`);
}

export function reprocessDocument(id: string): Promise<{ status: string; id: string }> {
  return apiPost(`/api/documents/${id}/reprocess`);
}

export interface ProgressEvent {
  status: string;
  message?: string;
  chunk_count?: number;
  error?: string;
}

export function subscribeProgress(
  docId: string,
  onEvent: (event: ProgressEvent) => void,
  onDone: () => void,
): () => void {
  const controller = new AbortController();
  let cancelled = false;
  let finished = false;
  let retryMs = 3000;

  const finish = () => {
    if (finished) return;
    finished = true;
    controller.abort();
    onDone();
  };

  const handleBlock = (block: string) => {
    if (!block || block.startsWith(":")) return;
    const retry = block.match(/^retry:\s*(\d+)/m);
    if (retry) retryMs = Number(retry[1]);
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return;
    try {
      const event = JSON.parse(data) as ProgressEvent;
      onEvent(event);
      if (["ready", "failed", "waiting_for_ocr", "not_found"].includes(event.status)) {
        finish();
      }
    } catch {
      // ignore parse errors
    }
  };

  const waitToReconnect = () => new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, retryMs);
    controller.signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });

  const run = async () => {
    while (!cancelled && !finished) {
      try {
        const response = await fetchWithAuth(`/api/documents/${docId}/progress`, {
          signal: controller.signal,
        });
        if (response.status === 401) {
          finish();
          return;
        }
        if (!response.ok || !response.body) {
          throw new Error(`Progress stream failed: ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!cancelled && !finished) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            handleBlock(buffer.slice(0, boundary));
            buffer = buffer.slice(boundary + 2);
            boundary = buffer.indexOf("\n\n");
          }
          if (done) break;
        }
      } catch (error) {
        if (controller.signal.aborted || cancelled || finished) return;
        void error;
      }
      if (!cancelled && !finished) await waitToReconnect();
    }
  };
  void run();

  return () => {
    cancelled = true;
    finished = true;
    controller.abort();
  };
}
