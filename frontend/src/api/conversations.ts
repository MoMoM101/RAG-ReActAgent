import { apiGet, apiPost, apiDelete } from "./client";

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
  const res = await fetch(`/api/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`PATCH: ${res.status}`);
  return res.json();
}
