import type { SSEEvent } from "../types";
import { fetchWithAuth } from "../stores/authStore";

export function sendMessage(
  message: string,
  conversationId: string | null,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onDone: () => void,
  onConvId?: (id: string) => void,
): AbortController {
  const controller = new AbortController();

  fetchWithAuth("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`${response.status} - ${text}`);
      }
      const convId = response.headers.get("X-Conversation-Id");
      if (convId) onConvId?.(convId);
      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";
      // Keep the event name across network chunks. An SSE frame may be split
      // between its `event:` and `data:` lines.
      let eventType = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              onEvent({ event: eventType, data });
              eventType = "";
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
