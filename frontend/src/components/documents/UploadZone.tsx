import { useState, useCallback } from "react";
import { useDocumentStore } from "../../stores/documentStore";
import { UploadIcon } from "../shared/Icons";

const ACCEPT = ".pdf,.docx,.txt,.md,.csv,.xlsx";

export function UploadZone() {
  const { upload, uploading, uploadProgress } = useDocumentStore();
  const [dragging, setDragging] = useState(false);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    Array.from(e.dataTransfer.files).forEach((f) => upload(f));
  }, [upload]);

  const handleFile = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    Array.from(e.target.files || []).forEach((f) => upload(f));
  }, [upload]);

  return (
    <div
      className={`upload-zone ${dragging ? "dragging" : ""}`}
      onDrop={handleDrop}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onClick={() => document.getElementById("file-input")?.click()}
    >
      {uploading ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <div style={{ width: 32, height: 32, borderRadius: "50%", border: "2px solid var(--accent)", borderTopColor: "transparent", animation: "spin 0.8s linear infinite" }} />
          <p style={{ fontSize: 13, color: "var(--fg)" }}>
            {uploadProgress?.message || "处理中..."}
          </p>
          <div className="progress-steps" style={{ display: "flex", gap: 16, fontSize: 12 }}>
            {[
              { key: "parsing", label: "解析文档" },
              { key: "chunking", label: "切分文本" },
              { key: "embedding", label: "向量化" },
              { key: "indexing", label: "写入索引" },
            ].map((step, i) => {
              const stepOrder = ["uploaded", "parsing", "chunking", "embedding", "indexing"];
              const currentIdx = stepOrder.indexOf(uploadProgress?.status || "");
              const done = currentIdx > i;
              const active = currentIdx === i;
              return (
                <span key={step.key} style={{
                  color: done ? "var(--success)" : active ? "var(--accent)" : "var(--muted)",
                  fontWeight: active ? 600 : 400,
                }}>
                  {done ? "✓" : active ? "●" : "○"} {step.label}
                </span>
              );
            })}
          </div>
        </div>
      ) : (
        <>
          <UploadIcon size={28} style={{ color: "var(--muted)", marginBottom: 6 }} />
          <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 2 }}>
            {dragging ? "释放以上传文件" : "拖放文件或点击上传"}
          </h3>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>最大 50MB</p>
          <div className="upload-types">
            {["PDF", "DOCX", "TXT", "MD", "CSV", "XLSX"].map((t) => (
              <span key={t} className="type-tag">{t}</span>
            ))}
          </div>
        </>
      )}
      <input
        id="file-input"
        type="file"
        style={{ display: "none" }}
        accept={ACCEPT}
        multiple
        onChange={handleFile}
        disabled={uploading}
      />
    </div>
  );
}
