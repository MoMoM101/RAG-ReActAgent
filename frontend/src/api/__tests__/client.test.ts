import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiUpload } from "../client";

class FakeXMLHttpRequest {
  static instance: FakeXMLHttpRequest;

  status = 200;
  responseText = '{"id":"doc-1"}';
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open = vi.fn();
  setRequestHeader = vi.fn();
  send = vi.fn();
  abort = vi.fn(() => this.onabort?.());

  constructor() {
    FakeXMLHttpRequest.instance = this;
  }
}

describe("apiUpload", () => {
  beforeEach(() => {
    vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);
  });

  it("reports upload percentage and resolves the JSON response", async () => {
    const onProgress = vi.fn();
    const request = apiUpload<{ id: string }>("/upload", new FormData(), onProgress);
    const xhr = FakeXMLHttpRequest.instance;

    xhr.upload.onprogress?.({
      lengthComputable: true,
      loaded: 42,
      total: 100,
    } as ProgressEvent);
    xhr.onload?.();

    await expect(request).resolves.toEqual({ id: "doc-1" });
    expect(onProgress).toHaveBeenCalledWith(42);
  });

  it("aborts the underlying request through AbortSignal", async () => {
    const controller = new AbortController();
    const request = apiUpload("/upload", new FormData(), undefined, controller.signal);
    const xhr = FakeXMLHttpRequest.instance;

    controller.abort();

    expect(xhr.abort).toHaveBeenCalledOnce();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
  });
});
