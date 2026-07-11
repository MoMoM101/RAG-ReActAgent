import { useState, useEffect, useCallback } from "react";
import { listMemories, updateMemory, deleteMemory, clearAllMemories, type MemoryEntry } from "../../api/memories";
import { useConfirm } from "../shared/useConfirm";
import { useToastStore } from "../../stores/toastStore";
import { TrashIcon, EditIcon } from "../shared/Icons";
import { Skeleton } from "../shared/Skeleton";

export function MemoryList() {
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const confirm = useConfirm();
  const addToast = useToastStore((s) => s.addToast);

  const load = useCallback(async () => {
    try {
      const data = await listMemories();
      setMemories(data.memories);
    } catch {
      addToast({ type: "error", message: "加载记忆失败" });
    }
    setLoading(false);
  }, [addToast]);

  useEffect(() => { load(); }, [load]);

  const handleStartEdit = (m: MemoryEntry) => {
    setEditingId(m.id);
    setEditContent(m.content);
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditContent("");
  };

  const handleSaveEdit = async (id: string) => {
    if (!editContent.trim()) return;
    setSaving(true);
    try {
      const updated = await updateMemory(id, editContent.trim());
      setMemories((prev) =>
        prev.map((m) => (m.id === id ? { ...m, content: updated.content } : m))
      );
      setEditingId(null);
      setEditContent("");
      addToast({ type: "success", message: "已更新" });
    } catch {
      addToast({ type: "error", message: "更新失败" });
    }
    setSaving(false);
  };

  const handleDelete = async (id: string, content: string) => {
    const ok = await confirm({
      title: "删除记忆",
      message: `确定删除「${content.slice(0, 50)}...」？`,
      variant: "danger",
      confirmLabel: "删除",
    });
    if (!ok) return;
    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
      addToast({ type: "success", message: "已删除" });
    } catch {
      addToast({ type: "error", message: "删除失败" });
    }
  };

  const handleClearAll = async () => {
    const ok = await confirm({
      title: "清空全部记忆",
      message: `确定删除全部 ${memories.length} 条记忆？此操作不可恢复。`,
      variant: "danger",
      confirmLabel: "全部删除",
      cancelLabel: "取消",
    });
    if (!ok) return;
    try {
      const result = await clearAllMemories();
      setMemories([]);
      addToast({ type: "success", message: `已清空 ${result.count} 条记忆` });
    } catch {
      addToast({ type: "error", message: "清空失败" });
    }
  };

  return (
    <div className="chat-main">
      <div className="chat-header">
        <h2 className="chat-header-title">记忆管理</h2>
      </div>
      <div className="settings-content">
        {loading ? (
          <Skeleton height={48} count={4} />
        ) : memories.length === 0 ? (
          <p style={{ color: "var(--muted)", fontSize: 14 }}>
            暂无记忆。当 AI 在对话中调用 save_to_memory 保存信息后，这里会显示。
          </p>
        ) : (
          <div className="settings-section">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <span style={{ color: "var(--muted)", fontSize: 12 }}>
                共 {memories.length} 条记忆
              </span>
              <button
                onClick={handleClearAll}
                style={{
                  fontSize: 12, padding: "4px 12px",
                  background: "transparent", color: "var(--danger)",
                  border: "1px solid var(--danger)", borderRadius: 4,
                  cursor: "pointer",
                }}
              >
                清空全部
              </button>
            </div>
            {memories.map((m) => (
              <div key={m.id} style={{
                display: "flex", alignItems: "flex-start", gap: 10,
                padding: "10px 0", borderBottom: "1px solid var(--border)",
              }}>
                {editingId === m.id ? (
                  <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
                    <textarea
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      rows={3}
                      style={{
                        width: "100%", padding: 8, fontSize: 13,
                        background: "var(--surface)", color: "var(--fg)",
                        border: "1px solid var(--border)", borderRadius: 4,
                        resize: "vertical",
                      }}
                    />
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        onClick={() => handleSaveEdit(m.id)}
                        disabled={saving || !editContent.trim()}
                        style={{
                          fontSize: 12, padding: "3px 12px",
                          background: "var(--accent)", color: "#fff",
                          border: "none", borderRadius: 4, cursor: "pointer",
                        }}
                      >
                        {saving ? "保存中..." : "保存"}
                      </button>
                      <button
                        onClick={handleCancelEdit}
                        style={{
                          fontSize: 12, padding: "3px 12px",
                          background: "transparent", color: "var(--muted)",
                          border: "1px solid var(--border)", borderRadius: 4,
                          cursor: "pointer",
                        }}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div style={{ flex: 1, fontSize: 13 }}>
                      <div style={{ marginBottom: 4 }}>{m.content}</div>
                      <span style={{ fontSize: 11, color: "var(--muted)" }}>
                        {new Date(m.created_at).toLocaleString()}
                      </span>
                    </div>
                    <button
                      onClick={() => handleStartEdit(m)}
                      style={{
                        background: "transparent", border: "none", cursor: "pointer",
                        color: "var(--muted)", padding: 4, flexShrink: 0,
                      }}
                      title="编辑"
                    >
                      <EditIcon size={14} />
                    </button>
                    <button
                      onClick={() => handleDelete(m.id, m.content)}
                      style={{
                        background: "transparent", border: "none", cursor: "pointer",
                        color: "var(--muted)", padding: 4, flexShrink: 0,
                      }}
                      title="删除"
                    >
                      <TrashIcon size={14} />
                    </button>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
