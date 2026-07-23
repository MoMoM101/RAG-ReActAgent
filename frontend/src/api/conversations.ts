import { apiDelete, apiGet, apiPatch, apiPost } from "./client";

export interface ConvResponse {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export function listConversations(): Promise<ConvResponse[]> {
  return apiGet("/api/conversations");
}

export function createConversation(title?: string): Promise<ConvResponse> {
  return apiPost("/api/conversations", { title: title || "New Chat" });
}

export function deleteAllConversations(): Promise<{ status: string; count: number }> {
  return apiDelete("/api/conversations");
}

export function deleteConversation(id: string): Promise<void> {
  return apiDelete(`/api/conversations/${id}`);
}

export function getMessages(convId: string): Promise<unknown[]> {
  return apiGet(`/api/conversations/${convId}/messages`);
}

export async function renameConversation(id: string, title: string): Promise<{ id: string; title: string }> {
  return apiPatch(`/api/conversations/${id}`, { title });
}
