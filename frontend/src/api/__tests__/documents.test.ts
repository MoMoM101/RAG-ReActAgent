import { beforeEach, describe, expect, it, vi } from "vitest";

import { subscribeProgress } from "../documents";

function streamResponse(block: string, keepOpen = false) {
  const encoded = new TextEncoder().encode(block);
  let emitted = false;
  return {
    status: 200,
    ok: true,
    body: {
      getReader: () => ({
        read: vi.fn(async () => {
          if (!emitted) {
            emitted = true;
            return { done: false, value: encoded };
          }
          if (keepOpen) return new Promise<never>(() => undefined);
          return { done: true, value: undefined };
        }),
      }),
    },
  };
}

describe("subscribeProgress", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("sends the bearer token and completes once on terminal state", async () => {
    sessionStorage.setItem("rag_access_token", "access-token");
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse('retry: 3000\n\ndata: {"status":"ready","chunk_count":4}\n\n'),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onEvent = vi.fn();
    const onDone = vi.fn();

    subscribeProgress("doc-1", onEvent, onDone);

    await vi.waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(onEvent).toHaveBeenCalledWith({ status: "ready", chunk_count: 4 });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/documents/doc-1/progress",
      expect.objectContaining({
        headers: { authorization: "Bearer access-token" },
      }),
    );
  });

  it("does not treat a legacy timeout event as task completion", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      streamResponse('data: {"status":"timeout"}\n\n', true),
    ));
    const onEvent = vi.fn();
    const onDone = vi.fn();

    const cleanup = subscribeProgress("doc-1", onEvent, onDone);

    await vi.waitFor(() => {
      expect(onEvent).toHaveBeenCalledWith({ status: "timeout" });
    });
    expect(onDone).not.toHaveBeenCalled();
    cleanup();
  });
});
