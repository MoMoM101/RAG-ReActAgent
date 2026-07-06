import { apiGet, apiPost, apiDelete } from "./client";
import type { Document, DocumentChunks } from "../types/document";

export function listDocuments(): Promise<Document[]> {
  return apiGet("/api/documents");
}

export function uploadDocument(file: File): Promise<Document> {
  const form = new FormData();
  form.append("file", file);
  return apiPost("/api/documents/upload", form);
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

export function reprocessDocument(id: string): Promise<{ status: string }> {
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
  const es = new EventSource(`/api/documents/${docId}/progress`);

  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data) as ProgressEvent;
      onEvent(data);
      if (data.status === "ready" || data.status === "failed" || data.status === "timeout") {
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
