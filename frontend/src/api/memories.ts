import { apiGet, apiPut, apiDelete } from "./client";

export interface MemoryEntry {
  id: string;
  content: string;
  conversation_id: string | null;
  created_at: string;
}

export function listMemories(): Promise<{ memories: MemoryEntry[] }> {
  return apiGet("/api/memories");
}

export function updateMemory(id: string, content: string): Promise<MemoryEntry> {
  return apiPut(`/api/memories/${id}`, { content });
}

export function deleteMemory(id: string): Promise<{ status: string }> {
  return apiDelete(`/api/memories/${id}`);
}

export function clearAllMemories(): Promise<{ status: string; count: number }> {
  return apiDelete("/api/memories");
}
