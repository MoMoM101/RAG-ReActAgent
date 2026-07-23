import { useState, useEffect } from "react";
import type { DocumentChunks } from "../../types/document";
import { useDocumentStore } from "../../stores/documentStore";
import { CloseIcon } from "../shared/Icons";
import { Skeleton } from "../shared/Skeleton";

interface Props { docId: string; onClose: () => void }

export function ChunkViewer({ docId, onClose }: Props) {
  const [data, setData] = useState<DocumentChunks | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { getChunks } = useDocumentStore();

  useEffect(() => {
    getChunks(docId)
      .then((d) => setData(d as DocumentChunks))
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
      .finally(() => setLoading(false));
  }, [docId, getChunks]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">文档分块 — {data?.filename || "加载中..."}</span>
          <button className="modal-close" onClick={onClose}><CloseIcon size={16} /></button>
        </div>
        <div className="modal-body">
          {loading
            ? <Skeleton height={60} count={3} />
          : error
            ? <p style={{ color: "var(--danger)", fontSize: 13 }}>{error}</p>
          : !data || data.chunks.length === 0
            ? <p style={{ color: "var(--muted)", fontSize: 13 }}>暂无分块内容</p>
            : data.chunks.map((chunk, i) => (
                <div
                  key={i}
                  style={{
                    padding: "9px 12px",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius)",
                    marginBottom: 7,
                    fontSize: 13,
                    lineHeight: 1.6,
                    background: "var(--overlay-subtle)",
                  }}
                >
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", letterSpacing: "0.04em", marginBottom: 3 }}>
                    chunk_{(i + 1).toString().padStart(3, "0")} · {chunk.text.length} 字
                  </div>
                  <div style={{ whiteSpace: "pre-wrap" }}>{chunk.text}</div>
                </div>
              ))
          }
        </div>
      </div>
    </div>
  );
}
