import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ fetchWithAuth: vi.fn() }));
vi.mock("../../stores/authStore", () => auth);

import { sendMessage } from "../chat";

describe("chat SSE parsing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("preserves the event type when event and data lines arrive separately", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode("event: answer_chunk\n"));
        controller.enqueue(encoder.encode('data: {"delta":"正文"}\n\n'));
        controller.enqueue(encoder.encode("event: done\n"));
        controller.enqueue(encoder.encode("data: {}\n\n"));
        controller.close();
      },
    });
    auth.fetchWithAuth.mockResolvedValue(
      new Response(body, {
        status: 200,
        headers: { "X-Conversation-Id": "conv-1" },
      }),
    );

    const events: Array<{ event: string; data: unknown }> = [];
    await new Promise<void>((resolve, reject) => {
      sendMessage("问题", null, (event) => events.push(event), reject, resolve);
    });

    expect(events).toEqual([
      { event: "answer_chunk", data: { delta: "正文" } },
      { event: "done", data: {} },
    ]);
  });
});
