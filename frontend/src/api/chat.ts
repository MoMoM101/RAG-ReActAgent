import type { SSEEvent } from "../types";
import { authHeaders } from "../stores/authStore";

export function sendMessage(
  message: string,
  conversationId: string | null,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onDone: () => void,
  onConvId?: (id: string) => void,
): AbortController {
  const controller = new AbortController();

  fetch("/api/chat", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        if (response.status === 401) {
          sessionStorage.removeItem("rag_admin_token");
          window.dispatchEvent(new CustomEvent("auth:required"));
        }
        const text = await response.text();
        throw new Error(`${response.status} - ${text}`);
      }
      const convId = response.headers.get("X-Conversation-Id");
      if (convId) onConvId?.(convId);
      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              onEvent({ event: eventType, data });
            } catch {
              // skip partial
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== "AbortError") onError(err);
    })
    .finally(onDone);

  return controller;
}
