import { create } from "zustand";
import type { Document } from "../types/document";
import { clearAllDocuments, deleteDocument, getDocumentChunks, getUploadConfig, listDocuments, reprocessDocument, subscribeProgress, uploadDocument, uploadDocuments } from "../api/documents";
import type { ProgressEvent } from "../api/documents";
import { ApiError } from "../api/client";
import { useToastStore } from "./toastStore";

interface DocumentStore {
  documents: Document[];
  uploading: boolean;
  uploadPercent: number | null;
  maxUploadMb: number;
  batchMaxFiles: number;
  batchMaxTotalMb: number;
  uploadingFiles: string[];
  uploadProgress: { status: string; message?: string } | null;
  activeUploadDocId: string | null;
  reprocessing: Record<string, boolean>;

  load: () => Promise<void>;
  loadUploadConfig: () => Promise<void>;
  upload: (file: File) => Promise<void>;
  uploadMany: (files: File[]) => Promise<void>;
  remove: (id: string) => Promise<void>;
  clearAll: () => Promise<number>;
  reprocess: (id: string) => Promise<void>;
  getChunks: (id: string) => Promise<unknown>;
  cancelUploadProgress: () => void;
  cancelUpload: () => void;
}

let _sseCleanup: (() => void) | null = null;
let _uploadAbortController: AbortController | null = null;
const _reprocessCleanups = new Map<string, () => void>();
const _batchUploadCleanups = new Map<string, () => void>();
let _loadSequence = 0;
let _mutationVersion = 0;

export const useDocumentStore = create<DocumentStore>((set, get) => ({
  documents: [],
  uploading: false,
  uploadPercent: null,
  maxUploadMb: 200,
  batchMaxFiles: 50,
  batchMaxTotalMb: 1024,
  uploadingFiles: [],
  uploadProgress: null,
  activeUploadDocId: null,
  reprocessing: {},

  load: async () => {
    const requestId = ++_loadSequence;
    const mutationAtStart = _mutationVersion;
    try {
      const docs = await listDocuments();
      if (requestId !== _loadSequence || mutationAtStart !== _mutationVersion) {
        return;
      }
      const activeUploadDocId = get().activeUploadDocId;
      const observedUpload = activeUploadDocId
        ? docs.find((doc) => doc.id === activeUploadDocId)
        : undefined;
      const uploadReachedTerminal = observedUpload
        ? observedUpload.status === "ready"
          || observedUpload.status === "failed"
          || observedUpload.status === "waiting_for_ocr"
        : false;
      const uploadMissing = Boolean(activeUploadDocId && !observedUpload);
      const uploadSettled = uploadReachedTerminal || uploadMissing;
      if (uploadSettled && _sseCleanup) {
        _sseCleanup();
        _sseCleanup = null;
      }
      const activeIds = new Set(
        docs
          .filter((doc) => !["ready", "failed", "waiting_for_ocr"].includes(doc.status))
          .map((doc) => doc.id),
      );
      for (const [id, cleanup] of _reprocessCleanups) {
        if (!activeIds.has(id)) {
          cleanup();
          _reprocessCleanups.delete(id);
        }
      }
      set((state) => ({
        documents: docs,
        reprocessing: Object.fromEntries(
          Object.entries(state.reprocessing).filter(([id]) => activeIds.has(id)),
        ),
        ...(uploadSettled ? {
          uploading: false,
          uploadPercent: null,
          uploadProgress: null,
          activeUploadDocId: null,
        } : {}),
      }));
    } catch { /* ignore */ }
  },

  loadUploadConfig: async () => {
    try {
      const config = await getUploadConfig();
      set({
        maxUploadMb: config.max_upload_mb,
        batchMaxFiles: config.batch_max_files ?? 50,
        batchMaxTotalMb: config.batch_max_total_mb ?? 1024,
      });
    } catch { /* retain safe default */ }
  },

  cancelUploadProgress: () => {
    if (_sseCleanup) {
      _sseCleanup();
      _sseCleanup = null;
    }
  },

  cancelUpload: () => {
    _uploadAbortController?.abort();
  },

  uploadMany: async (files: File[]) => {
    if (files.length === 0) return;
    const { maxUploadMb, batchMaxFiles, batchMaxTotalMb } = get();
    if (files.length > batchMaxFiles) {
      useToastStore.getState().addToast({
        type: "error",
        message: `单批最多上传 ${batchMaxFiles} 个文件`,
      });
      return;
    }
    const oversized = files.find((file) => file.size > maxUploadMb * 1024 * 1024);
    if (oversized) {
      useToastStore.getState().addToast({
        type: "error",
        message: `「${oversized.name}」超过 ${maxUploadMb} MB 单文件上限`,
      });
      return;
    }
    const totalBytes = files.reduce((total, file) => total + file.size, 0);
    if (totalBytes > batchMaxTotalMb * 1024 * 1024) {
      useToastStore.getState().addToast({
        type: "error",
        message: `本批文件总大小超过 ${batchMaxTotalMb} MB`,
      });
      return;
    }

    _uploadAbortController = new AbortController();
    set({
      uploading: true,
      uploadingFiles: files.map((file) => file.name),
      uploadPercent: 0,
      uploadProgress: {
        status: "uploading",
        message: `正在上传 ${files.length} 个文件 · 0%`,
      },
    });
    try {
      const result = await uploadDocuments(
        files,
        (percent) => set({
          uploadPercent: percent,
          uploadProgress: {
            status: "uploading",
            message: `正在上传 ${files.length} 个文件 · ${percent}%`,
          },
        }),
        _uploadAbortController.signal,
      );
      _uploadAbortController = null;
      _mutationVersion += 1;
      const successful = result.items.flatMap((item) =>
        item.success && item.document ? [item.document] : [],
      );
      set((state) => ({
        documents: [
          ...successful,
          ...state.documents.filter(
            (existing) => !successful.some((doc) => doc.id === existing.id),
          ),
        ],
        uploading: false,
        uploadingFiles: [],
        uploadPercent: null,
        uploadProgress: null,
      }));

      const addToast = useToastStore.getState().addToast;
      for (const item of result.items) {
        if (!item.success || !item.document) {
          addToast({
            type: "error",
            message: `「${item.filename}」上传失败：${item.error || "未知错误"}`,
          });
          continue;
        }
        const doc = item.document;
        const cleanup = subscribeProgress(
          doc.id,
          (event: ProgressEvent) => {
            _mutationVersion += 1;
            set((state) => ({
              documents: state.documents.map((current) =>
                current.id === doc.id
                  ? {
                      ...current,
                      status: event.status,
                      chunk_count: event.chunk_count ?? current.chunk_count,
                      error_message: event.error,
                    }
                  : current,
              ),
            }));
          },
          async () => {
            _batchUploadCleanups.delete(doc.id);
            await get().load();
            const updated = get().documents.find((current) => current.id === doc.id);
            addToast({
              type: updated?.status === "ready" ? "success" : "error",
              message: updated?.status === "ready"
                ? `「${doc.filename}」处理完成`
                : `「${doc.filename}」处理失败`,
            });
          },
        );
        _batchUploadCleanups.set(doc.id, cleanup);
      }
      addToast({
        type: result.failed ? "info" : "success",
        message: `批量上传完成：成功 ${result.succeeded} 个，失败 ${result.failed} 个`,
      });
    } catch (error) {
      _uploadAbortController = null;
      const aborted = error instanceof DOMException && error.name === "AbortError";
      useToastStore.getState().addToast({
        type: aborted ? "info" : "error",
        message: aborted ? "已取消批量上传" : (error instanceof Error ? error.message : "批量上传失败"),
      });
      set({
        uploading: false,
        uploadingFiles: [],
        uploadPercent: null,
        uploadProgress: null,
      });
      await get().load();
    }
  },

  upload: async (file: File) => {
    const maxUploadMb = get().maxUploadMb;
    if (file.size > maxUploadMb * 1024 * 1024) {
      useToastStore.getState().addToast({
        type: "error",
        message: `「${file.name}」超过 ${maxUploadMb} MB 上传上限`,
      });
      return;
    }
    // 取消上一次上传的 SSE 进度订阅
    if (_sseCleanup) {
      _sseCleanup();
      _sseCleanup = null;
    }
    _uploadAbortController = new AbortController();
    set({
      uploading: true,
      uploadingFiles: [file.name],
      uploadPercent: 0,
      uploadProgress: { status: "uploading", message: "正在上传 0%" },
    });
    try {
      const doc = await uploadDocument(
        file,
        (percent) => set({
          uploadPercent: percent,
          uploadProgress: { status: "uploading", message: `正在上传 ${percent}%` },
        }),
        _uploadAbortController.signal,
      );
      _uploadAbortController = null;
      _mutationVersion += 1;
      set((state) => ({
        uploadPercent: 100,
        uploadProgress: { status: "uploaded", message: "上传完成，等待处理" },
        // The upload response is already the durable server document. Insert
        // it immediately so subsequent SSE events have a visible row to update.
        documents: [doc, ...state.documents.filter((item) => item.id !== doc.id)],
        activeUploadDocId: doc.id,
      }));
      const addToast = useToastStore.getState().addToast;
      addToast({ type: "success", message: `「${file.name}」上传成功` });

      _sseCleanup = subscribeProgress(
        doc.id,
        (event: ProgressEvent) => {
          _mutationVersion += 1;
          set((state) => ({
            uploadProgress: { status: event.status, message: event.message },
            documents: state.documents.map((d) =>
              d.id === doc.id
                ? {
                    ...d,
                    status: event.status,
                    chunk_count: event.chunk_count ?? d.chunk_count,
                    error_message: event.error,
                  }
                : d,
            ),
          }));
        },
        async () => {
          _sseCleanup = null;
          await get().load();
          const updated = get().documents.find((d) => d.id === doc.id);
          if (updated?.status === "ready") {
            addToast({ type: "success", message: `「${file.name}」处理完成` });
          } else if (updated?.status === "failed") {
            addToast({ type: "error", message: `「${file.name}」处理失败: ${updated.error_message || "未知错误"}` });
          } else if (updated?.status === "waiting_for_ocr") {
            addToast({ type: "info", message: `「${file.name}」正在等待 OCR 模型，模型就绪后会自动继续` });
          }
          set({ uploading: false, uploadPercent: null, uploadProgress: null });
          set({ activeUploadDocId: null, uploadingFiles: [] });
        },
      );
    } catch (e: unknown) {
      _uploadAbortController = null;
      const addToast = useToastStore.getState().addToast;
      if (e instanceof DOMException && e.name === "AbortError") {
        addToast({ type: "info", message: `已取消上传「${file.name}」` });
      } else if (e instanceof ApiError && e.status === 409) {
        addToast({ type: "error", message: `「${file.name}」已存在，无需重复上传` });
      } else {
        const msg = e instanceof Error ? e.message : "上传失败";
        addToast({ type: "error", message: msg });
      }
      await get().load();
      set({
        uploading: false,
        uploadPercent: null,
        uploadProgress: null,
        activeUploadDocId: null,
        uploadingFiles: [],
      });
    }
  },

  remove: async (id: string) => {
    await deleteDocument(id);
    _mutationVersion += 1;
    await get().load();
  },

  clearAll: async () => {
    const result = await clearAllDocuments();
    _mutationVersion += 1;
    _loadSequence += 1;
    _sseCleanup?.();
    _sseCleanup = null;
    _uploadAbortController?.abort();
    _uploadAbortController = null;
    for (const cleanup of _reprocessCleanups.values()) cleanup();
    _reprocessCleanups.clear();
    for (const cleanup of _batchUploadCleanups.values()) cleanup();
    _batchUploadCleanups.clear();
    set({
      documents: [],
      uploading: false,
      uploadPercent: null,
      uploadProgress: null,
      activeUploadDocId: null,
      uploadingFiles: [],
      reprocessing: {},
    });
    return result.count;
  },

  reprocess: async (id: string) => {
    _reprocessCleanups.get(id)?.();
    _reprocessCleanups.delete(id);
    _mutationVersion += 1;
    set((state) => ({
      reprocessing: { ...state.reprocessing, [id]: true },
      documents: state.documents.map((doc) =>
        doc.id === id
          ? { ...doc, status: "uploaded", error_message: undefined }
          : doc,
      ),
    }));

    try {
      await reprocessDocument(id);
      const cleanup = subscribeProgress(
        id,
        (event) => {
          _mutationVersion += 1;
          set((state) => ({
            documents: state.documents.map((doc) =>
              doc.id === id
                ? {
                    ...doc,
                    status: event.status,
                    chunk_count: event.chunk_count ?? doc.chunk_count,
                    error_message: event.error,
                  }
                : doc,
            ),
          }));
        },
        async () => {
          _reprocessCleanups.delete(id);
          await get().load();
          set((state) => {
            const next = { ...state.reprocessing };
            delete next[id];
            return { reprocessing: next };
          });
        },
      );
      _reprocessCleanups.set(id, cleanup);
    } catch (error) {
      set((state) => {
        const next = { ...state.reprocessing };
        delete next[id];
        return { reprocessing: next };
      });
      await get().load();
      throw error;
    }
  },

  getChunks: async (id: string) => {
    return getDocumentChunks(id);
  },
}));
