import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listDocuments: vi.fn(),
  uploadDocument: vi.fn(),
  uploadDocuments: vi.fn(),
  deleteDocument: vi.fn(),
  clearAllDocuments: vi.fn(),
  getDocumentChunks: vi.fn(),
  getUploadConfig: vi.fn(),
  reprocessDocument: vi.fn(),
  subscribeProgress: vi.fn(),
}));

vi.mock("../../api/documents", () => api);

import type { Document } from "../../types/document";
import type { ProgressEvent } from "../../api/documents";
import { useDocumentStore } from "../documentStore";

const failedDocument: Document = {
  id: "doc-1",
  filename: "failed.txt",
  file_size: 100,
  file_type: ".txt",
  status: "failed",
  chunk_count: 0,
  error_message: "embedding failed",
  created_at: "2026-07-16T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  useDocumentStore.setState({
    documents: [failedDocument],
    uploading: false,
    uploadPercent: null,
    maxUploadMb: 200,
    batchMaxFiles: 50,
    batchMaxTotalMb: 1024,
    uploadingFiles: [],
    uploadProgress: null,
    activeUploadDocId: null,
    reprocessing: {},
  });
});

describe("documentStore reprocess", () => {
  it("clears stale reprocessing state when refresh observes a terminal status", async () => {
    useDocumentStore.setState({ reprocessing: { "doc-1": true } });
    api.listDocuments.mockResolvedValue([failedDocument]);

    await useDocumentStore.getState().load();

    expect(useDocumentStore.getState().reprocessing["doc-1"]).toBeUndefined();
  });

  it("updates immediately, follows progress, and refreshes on completion", async () => {
    let onEvent: ((event: ProgressEvent) => void) | undefined;
    let onDone: (() => void) | undefined;
    api.reprocessDocument.mockResolvedValue({ status: "queued", id: "doc-1" });
    api.subscribeProgress.mockImplementation(
      (_id: string, event: typeof onEvent, done: typeof onDone) => {
        onEvent = event;
        onDone = done;
        return vi.fn();
      },
    );
    api.listDocuments.mockResolvedValue([
      { ...failedDocument, status: "ready", chunk_count: 3, error_message: undefined },
    ]);

    const request = useDocumentStore.getState().reprocess("doc-1");
    expect(useDocumentStore.getState().documents[0].status).toBe("uploaded");
    expect(useDocumentStore.getState().reprocessing["doc-1"]).toBe(true);
    await request;

    onEvent?.({ status: "embedding", message: "正在向量化" });
    expect(useDocumentStore.getState().documents[0].status).toBe("embedding");

    await onDone?.();
    expect(api.listDocuments).toHaveBeenCalledOnce();
    expect(useDocumentStore.getState().documents[0].status).toBe("ready");
    expect(useDocumentStore.getState().reprocessing["doc-1"]).toBeUndefined();
  });

  it("restores server state when submitting retry fails", async () => {
    api.reprocessDocument.mockRejectedValue(new Error("retry failed"));
    api.listDocuments.mockResolvedValue([failedDocument]);

    await expect(
      useDocumentStore.getState().reprocess("doc-1"),
    ).rejects.toThrow("retry failed");

    expect(useDocumentStore.getState().documents[0].status).toBe("failed");
    expect(useDocumentStore.getState().reprocessing["doc-1"]).toBeUndefined();
  });
});

describe("documentStore upload limit", () => {
  it("loads the server limit and rejects oversized files before upload", async () => {
    api.getUploadConfig.mockResolvedValue({
      max_upload_mb: 100,
      hard_limit_mb: 512,
      allowed_extensions: [".pdf"],
    });
    await useDocumentStore.getState().loadUploadConfig();

    const oversized = new File([new Uint8Array(1)], "large.pdf");
    Object.defineProperty(oversized, "size", { value: 101 * 1024 * 1024 });
    await useDocumentStore.getState().upload(oversized);

    expect(useDocumentStore.getState().maxUploadMb).toBe(100);
    expect(api.uploadDocument).not.toHaveBeenCalled();
    expect(useDocumentStore.getState().uploading).toBe(false);
  });
});

describe("documentStore upload visibility", () => {
  it("uploads multiple files in one request and unlocks while processing continues", async () => {
    const first: Document = {
      id: "batch-1", filename: "one.pdf", file_size: 10, file_type: ".pdf",
      status: "uploaded", chunk_count: 0, created_at: "2026-07-16T12:00:00Z",
    };
    const second: Document = {
      id: "batch-2", filename: "two.txt", file_size: 20, file_type: ".txt",
      status: "uploaded", chunk_count: 0, created_at: "2026-07-16T12:00:01Z",
    };
    api.uploadDocuments.mockResolvedValue({
      items: [
        { filename: first.filename, success: true, document: first },
        { filename: second.filename, success: true, document: second },
      ],
      total: 2,
      succeeded: 2,
      failed: 0,
    });
    api.subscribeProgress.mockReturnValue(vi.fn());

    await useDocumentStore.getState().uploadMany([
      new File(["one"], "one.pdf"),
      new File(["two"], "two.txt"),
    ]);

    expect(api.uploadDocuments).toHaveBeenCalledOnce();
    expect(useDocumentStore.getState().documents.slice(0, 2).map((doc) => doc.id))
      .toEqual(["batch-1", "batch-2"]);
    expect(useDocumentStore.getState().uploading).toBe(false);
    expect(api.subscribeProgress).toHaveBeenCalledTimes(2);
  });

  it("inserts the returned document immediately and updates its progress", async () => {
    let onEvent: ((event: ProgressEvent) => void) | undefined;
    const uploadedDocument: Document = {
      id: "new-doc",
      filename: "manual.pdf",
      file_size: 4096,
      file_type: ".pdf",
      status: "uploaded",
      chunk_count: 0,
      created_at: "2026-07-16T12:00:00Z",
    };
    api.uploadDocument.mockResolvedValue(uploadedDocument);
    api.subscribeProgress.mockImplementation(
      (_id: string, event: typeof onEvent) => {
        onEvent = event;
        return vi.fn();
      },
    );

    await useDocumentStore.getState().upload(new File(["pdf"], "manual.pdf"));

    expect(useDocumentStore.getState().documents[0]).toMatchObject({
      id: "new-doc",
      filename: "manual.pdf",
      status: "uploaded",
    });

    onEvent?.({ status: "embedding", chunk_count: 6, message: "正在向量化" });
    expect(useDocumentStore.getState().documents[0]).toMatchObject({
      id: "new-doc",
      status: "embedding",
      chunk_count: 6,
    });
  });

  it("does not duplicate a row if the document already exists locally", async () => {
    const uploadedDocument: Document = {
      ...failedDocument,
      status: "uploaded",
      error_message: undefined,
    };
    api.uploadDocument.mockResolvedValue(uploadedDocument);
    api.subscribeProgress.mockReturnValue(vi.fn());

    await useDocumentStore.getState().upload(new File(["text"], "failed.txt"));

    expect(
      useDocumentStore.getState().documents.filter((doc) => doc.id === "doc-1"),
    ).toHaveLength(1);
  });

  it("preserves a new row from an older empty list response and unlocks on terminal refresh", async () => {
    const uploadedDocument: Document = {
      id: "race-doc",
      filename: "race.pdf",
      file_size: 1024,
      file_type: ".pdf",
      status: "uploaded",
      chunk_count: 0,
      created_at: "2026-07-16T12:00:00Z",
    };
    api.uploadDocument.mockResolvedValue(uploadedDocument);
    api.subscribeProgress.mockReturnValue(vi.fn());

    let resolveOldList: ((documents: Document[]) => void) | undefined;
    api.listDocuments.mockImplementationOnce(
      () => new Promise<Document[]>((resolve) => { resolveOldList = resolve; }),
    );
    const oldLoad = useDocumentStore.getState().load();
    await useDocumentStore.getState().upload(new File(["pdf"], "race.pdf"));
    resolveOldList?.([]);
    await oldLoad;
    expect(useDocumentStore.getState().documents[0].id).toBe("race-doc");
    expect(useDocumentStore.getState().uploading).toBe(true);

    api.listDocuments.mockResolvedValueOnce([
      { ...uploadedDocument, status: "ready", chunk_count: 8 },
    ]);
    await useDocumentStore.getState().load();
    expect(useDocumentStore.getState().documents[0].status).toBe("ready");
    expect(useDocumentStore.getState().uploading).toBe(false);
    expect(useDocumentStore.getState().activeUploadDocId).toBeNull();
  });

  it("clears upload state after a successful clear-all operation", async () => {
    api.clearAllDocuments.mockResolvedValue({ status: "cleared", count: 1 });
    useDocumentStore.setState({
      uploading: true,
      activeUploadDocId: "doc-1",
      uploadProgress: { status: "uploaded", message: "等待处理" },
    });

    const count = await useDocumentStore.getState().clearAll();

    expect(count).toBe(1);
    expect(useDocumentStore.getState()).toMatchObject({
      documents: [],
      uploading: false,
      activeUploadDocId: null,
      uploadProgress: null,
    });
  });

  it("removes a ghost upload when a current server response confirms it is missing", async () => {
    useDocumentStore.setState({
      documents: [{
        ...failedDocument,
        id: "ghost-doc",
        status: "uploaded",
      }],
      uploading: true,
      activeUploadDocId: "ghost-doc",
      uploadProgress: { status: "uploaded", message: "等待处理" },
    });
    api.listDocuments.mockResolvedValue([]);

    await useDocumentStore.getState().load();

    expect(useDocumentStore.getState()).toMatchObject({
      documents: [],
      uploading: false,
      activeUploadDocId: null,
      uploadProgress: null,
    });
  });
});
