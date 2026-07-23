import { useCallback, useEffect, useRef, useState } from "react";
import { useDocumentStore } from "../../stores/documentStore";
import { UploadIcon } from "../shared/Icons";

const ACCEPT = ".pdf,.docx,.txt,.md,.csv,.xlsx";

export function UploadZone() {
  const {
    uploadMany, uploading, uploadingFiles, uploadPercent, uploadProgress,
    cancelUpload, maxUploadMb, batchMaxFiles, batchMaxTotalMb, loadUploadConfig,
  } = useDocumentStore();
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadUploadConfig();
  }, [loadUploadConfig]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) uploadMany(files);
  }, [uploadMany]);

  const handleFile = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (files.length) uploadMany(files);
  }, [uploadMany]);

  return (
    <div
      className={`upload-zone ${dragging ? "dragging" : ""}`}
      onDrop={handleDrop}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onClick={() => fileInputRef.current?.click()}
    >
      {uploading ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <div style={{ width: 32, height: 32, borderRadius: "50%", border: "2px solid var(--accent)", borderTopColor: "transparent", animation: "spin 0.8s linear infinite" }} />
          <p style={{ fontSize: 13, color: "var(--fg)" }}>
            {uploadProgress?.message || "处理中..."}
          </p>
          {uploadingFiles.length > 0 && (
            <p style={{ fontSize: 11, color: "var(--muted)", maxWidth: 520, textAlign: "center" }}>
              {uploadingFiles.slice(0, 5).join("、")}
              {uploadingFiles.length > 5 ? ` 等 ${uploadingFiles.length} 个文件` : ""}
            </p>
          )}
          {uploadProgress?.status === "uploading" && (
            <>
              <div style={{ width: 260, maxWidth: "80%", height: 6, borderRadius: 3, background: "var(--border)", overflow: "hidden" }}>
                <div style={{ width: `${uploadPercent ?? 0}%`, height: "100%", background: "var(--accent)", transition: "width 120ms linear" }} />
              </div>
              <button
                type="button"
                className="doc-btn"
                onClick={(event) => {
                  event.stopPropagation();
                  cancelUpload();
                }}
              >
                取消上传
              </button>
            </>
          )}
          <div className="progress-steps" style={{ display: "flex", gap: 16, fontSize: 12 }}>
            {[
              { key: "parsing", label: "解析文档" },
              { key: "chunking", label: "切分文本" },
              { key: "embedding", label: "向量化" },
              { key: "indexing", label: "写入索引" },
            ].map((step, i) => {
              const stepOrder = ["parsing", "chunking", "embedding", "indexing"];
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
            {dragging ? "释放以批量上传" : "拖放多个文件或点击批量上传"}
          </h3>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            单文件最大 {maxUploadMb} MB；单批最多 {batchMaxFiles} 个、合计 {batchMaxTotalMb} MB
          </p>
          <div className="upload-types">
            {["PDF", "DOCX", "TXT", "MD", "CSV", "XLSX"].map((t) => (
              <span key={t} className="type-tag">{t}</span>
            ))}
          </div>
        </>
      )}
      <input
        ref={fileInputRef}
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
