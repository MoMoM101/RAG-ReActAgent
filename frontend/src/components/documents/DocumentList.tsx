import { useEffect, useRef, useState } from "react";
import { useDocumentStore } from "../../stores/documentStore";
import { UploadZone } from "./UploadZone";
import { ChunkViewer } from "./ChunkViewer";
import { useConfirm } from "../shared/useConfirm";
import { useToastStore } from "../../stores/toastStore";
import { TrashIcon, RefreshIcon } from "../shared/Icons";
import { Skeleton } from "../shared/Skeleton";

const STATUS_META: Record<string, { label: string; cls: string }> = {
  ready:     { label: "已完成", cls: "ready" },
  failed:    { label: "失败", cls: "failed" },
  waiting_for_ocr: { label: "等待 OCR", cls: "processing" },
  uploaded:  { label: "排队中", cls: "processing" },
  parsing:   { label: "解析中", cls: "processing" },
  chunking:  { label: "切块中", cls: "processing" },
  embedding: { label: "向量化", cls: "processing" },
  indexing:  { label: "索引中", cls: "processing" },
};

const TERMINAL_STATUSES = new Set(["ready", "failed"]);
const DELETABLE_STATUSES = new Set(["ready", "failed", "waiting_for_ocr"]);
const POLL_INTERVAL_MS = 3000;

function formatSize(bytes: number) {
  return bytes > 1048576
    ? `${(bytes / 1048576).toFixed(1)} MB`
    : `${(bytes / 1024).toFixed(0)} KB`;
}

function formatDate(iso: string) {
  return iso.replace("T", " ").slice(0, 16);
}

export function DocumentList() {
  const { documents, load, remove, clearAll, reprocess, reprocessing } = useDocumentStore();
  const [viewChunksId, setViewChunksId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [clearing, setClearing] = useState(false);
  const confirm = useConfirm();
  const addToast = useToastStore((s) => s.addToast);
  const hasPending = documents.some((doc) => !TERMINAL_STATUSES.has(doc.status));

  useEffect(() => {
    load().finally(() => setLoading(false));
  }, [load]);

  // 轮询兜底：当列表中有处理中的文档时，定时刷新状态
  // SSE 是主推送通道，此轮询作为 SSE 断开或丢事件时的后备
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    const hasPending = documents.some((d) => !TERMINAL_STATUSES.has(d.status));
    if (hasPending && !pollRef.current) {
      pollRef.current = setInterval(() => {
        const currentDocs = useDocumentStore.getState().documents;
        const stillPending = currentDocs.some(
          (d) => !TERMINAL_STATUSES.has(d.status),
        );
        if (!stillPending) {
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          return;
        }
        load();
      }, POLL_INTERVAL_MS);
    } else if (!hasPending && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [documents, load]);

  const handleDelete = async (id: string, filename: string) => {
    const ok = await confirm({
      title: "删除文档",
      message: `确定要删除「${filename}」吗？这将同时清除向量索引和全文索引。`,
      variant: "danger",
      confirmLabel: "删除",
    });
    if (ok) {
      await remove(id);
      addToast({ type: "success", message: "文档已删除" });
    }
  };

  const handleReprocess = async (id: string) => {
    try {
      await reprocess(id);
      addToast({ type: "success", message: "已提交重新处理" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "重试失败";
      addToast({ type: "error", message });
    }
  };

  const handleClearAll = async () => {
    const ok = await confirm({
      title: "清空全部文档",
      message: `确定要删除全部 ${documents.length} 个文档吗？此操作不可撤销。`,
      variant: "danger",
      confirmLabel: "全部删除",
    });
    if (!ok) return;

    setClearing(true);
    try {
      const count = await clearAll();
      addToast({ type: "success", message: `已清空 ${count} 个文档` });
    } catch {
      addToast({ type: "error", message: "清空失败，请检查后端服务" });
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="chat-main">
      <div className="chat-header">
        <span className="chat-header-title">知识库</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="status-badge ready">
            {documents.length} 个文档
          </span>
          {documents.length > 0 && (
            <button
              className="doc-btn danger"
              onClick={handleClearAll}
              disabled={clearing || hasPending}
              title={hasPending ? "存在处理中的文档，完成或失败后才能清空" : undefined}
            >
              {clearing ? "清空中..." : "清空全部"}
            </button>
          )}
        </div>
      </div>

      <div className="chat-messages" style={{ maxWidth: "none" }}>
        <UploadZone />

        {loading ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <Skeleton height={48} count={3} />
          </div>
        ) : documents.length === 0 ? (
          <div className="chat-empty" style={{ minHeight: 200 }}>
            <p>知识库暂无文件，可以批量上传文档</p>
          </div>
        ) : (
          <>
            {documents.length > 8 && (
              <div style={{ marginBottom: 10 }}>
                <input
                  type="text"
                  placeholder="搜索知识库文件..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "7px 12px",
                    fontSize: 13,
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius)",
                    color: "var(--fg)",
                    outline: "none",
                  }}
                />
              </div>
            )}
            <table className="doc-table">
              <thead>
                <tr>
                  <th>文件名</th><th>大小</th><th>类型</th><th>状态</th><th>分块</th><th>上传时间</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                {documents
                  .filter((d) => !search || d.filename.toLowerCase().includes(search.toLowerCase()))
                  .map((doc) => {
                const meta = STATUS_META[doc.status] || { label: doc.status, cls: "processing" };
                return (
                  <tr key={doc.id}>
                    <td><span className="doc-name">{doc.filename}</span></td>
                    <td><span className="doc-meta">{formatSize(doc.file_size)}</span></td>
                    <td><span className="doc-meta">{doc.file_type.replace(".", "").toUpperCase()}</span></td>
                    <td>
                      <span className={`status-badge ${meta.cls}`}>
                        <span className={`status-dot ${meta.cls}`} />
                        {meta.label}
                      </span>
                      {doc.error_message && (
                        <span style={{ fontSize: 10, color: "var(--danger)", marginLeft: 6, maxWidth: 100, overflow: "hidden", textOverflow: "ellipsis", display: "inline-block", verticalAlign: "middle" }}
                              title={doc.error_message}>
                          {doc.error_message}
                        </span>
                      )}
                    </td>
                    <td><span className="doc-meta">{doc.status === "ready" ? doc.chunk_count : "—"}</span></td>
                    <td><span className="doc-meta">{formatDate(doc.created_at)}</span></td>
                    <td>
                      <div className="doc-actions">
                        {doc.status === "ready" && (
                          <button className="doc-btn" onClick={() => setViewChunksId(doc.id)}>
                            分块
                          </button>
                        )}
                        {(doc.status === "failed" || doc.status === "waiting_for_ocr") && (
                          <button
                            className="doc-btn"
                            onClick={() => handleReprocess(doc.id)}
                            disabled={Boolean(reprocessing[doc.id])}
                          >
                            <RefreshIcon size={11} />
                            {reprocessing[doc.id] ? "提交中..." : doc.status === "waiting_for_ocr" ? "检查并重试" : "重试"}
                          </button>
                        )}
                        <button
                          className="doc-btn danger"
                          onClick={() => handleDelete(doc.id, doc.filename)}
                          disabled={
                            Boolean(reprocessing[doc.id])
                            || !DELETABLE_STATUSES.has(doc.status)
                          }
                        >
                          <TrashIcon size={11} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </>
        )}
      </div>

      {viewChunksId && (
        <ChunkViewer docId={viewChunksId} onClose={() => setViewChunksId(null)} />
      )}
    </div>
  );
}
