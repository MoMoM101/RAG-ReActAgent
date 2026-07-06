import { create } from "zustand";
import type { Document } from "../types/document";
import { listDocuments, uploadDocument, deleteDocument, getDocumentChunks, reprocessDocument, subscribeProgress } from "../api/documents";
import type { ProgressEvent } from "../api/documents";
import { ApiError } from "../api/client";
import { useToastStore } from "./toastStore";

interface DocumentStore {
  documents: Document[];
  uploading: boolean;
  uploadProgress: { status: string; message?: string } | null;

  load: () => Promise<void>;
  upload: (file: File) => Promise<void>;
  remove: (id: string) => Promise<void>;
  reprocess: (id: string) => Promise<void>;
  getChunks: (id: string) => Promise<unknown>;
  cancelUploadProgress: () => void;
}

let _sseCleanup: (() => void) | null = null;

export const useDocumentStore = create<DocumentStore>((set, get) => ({
  documents: [],
  uploading: false,
  uploadProgress: null,

  load: async () => {
    try {
      const docs = await listDocuments();
      set({ documents: docs });
    } catch { /* ignore */ }
  },

  cancelUploadProgress: () => {
    if (_sseCleanup) {
      _sseCleanup();
      _sseCleanup = null;
    }
  },

  upload: async (file: File) => {
    // 取消上一次上传的 SSE 进度订阅
    if (_sseCleanup) {
      _sseCleanup();
      _sseCleanup = null;
    }
    set({ uploading: true, uploadProgress: { status: "uploaded", message: "上传成功" } });
    try {
      const doc = await uploadDocument(file);
      const addToast = useToastStore.getState().addToast;
      addToast({ type: "success", message: `「${file.name}」上传成功` });

      _sseCleanup = subscribeProgress(
        doc.id,
        (event: ProgressEvent) => {
          set((state) => ({
            uploadProgress: { status: event.status, message: event.message },
            documents: state.documents.map((d) =>
              d.id === doc.id
                ? { ...d, status: event.status, chunk_count: event.chunk_count ?? d.chunk_count }
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
          }
          set({ uploading: false, uploadProgress: null });
        },
      );
    } catch (e: unknown) {
      const addToast = useToastStore.getState().addToast;
      if (e instanceof ApiError && e.status === 409) {
        addToast({ type: "error", message: `「${file.name}」已存在，无需重复上传` });
      } else {
        const msg = e instanceof Error ? e.message : "上传失败";
        addToast({ type: "error", message: msg });
      }
      await get().load();
      set({ uploading: false, uploadProgress: null });
    }
  },

  remove: async (id: string) => {
    await deleteDocument(id);
    await get().load();
  },

  reprocess: async (id: string) => {
    await reprocessDocument(id);
  },

  getChunks: async (id: string) => {
    return getDocumentChunks(id);
  },
}));
