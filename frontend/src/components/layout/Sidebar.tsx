import { useEffect, useState, useRef } from "react";
import { useChatStore } from "../../stores/chatStore";
import { useToastStore } from "../../stores/toastStore";
import { useConfirm } from "../shared/useConfirm";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ChatIcon, DocIcon, SettingsIcon, BrainIcon, PlusIcon, TrashIcon } from "../shared/Icons";

export function Sidebar() {
  const {
    conversations, loadConversations, newConversation,
    currentConvId, switchConversation, deleteConversation, deleteAllConversations, renameConversation,
  } = useChatStore();
  const addToast = useToastStore((s) => s.addToast);
  const confirm = useConfirm();
  const location = useLocation();
  const navigate = useNavigate();

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => { loadConversations(); }, [loadConversations]);

  useEffect(() => {
    if (editingId) editRef.current?.focus();
  }, [editingId]);

  const handleNew = async () => {
    await newConversation();
    addToast({ type: "success", message: "新对话已创建" });
  };

  const handleDelete = async (e: React.MouseEvent, id: string, title: string) => {
    e.stopPropagation();
    const ok = await confirm({
      title: "删除会话",
      message: `确定要删除「${title}」吗？此操作不可撤销。`,
      variant: "danger",
      confirmLabel: "删除",
    });
    if (ok) {
      await deleteConversation(id);
      addToast({ type: "success", message: "会话已删除" });
    }
  };

  const handleClearAll = async () => {
    const ok = await confirm({
      title: "清除所有会话",
      message: "确定要删除所有历史对话吗？此操作不可撤销。",
      variant: "danger",
      confirmLabel: "全部删除",
    });
    if (ok) {
      await deleteAllConversations();
      addToast({ type: "success", message: "所有会话已清除" });
      if (location.pathname === "/") navigate("/");
    }
  };

  const startRename = (e: React.MouseEvent, id: string, title: string) => {
    e.stopPropagation();
    setEditingId(id);
    setEditTitle(title);
  };

  const finishRename = async () => {
    if (editingId && editTitle.trim()) {
      await renameConversation(editingId, editTitle.trim());
    }
    setEditingId(null);
    setEditTitle("");
  };

  const handleRenameKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") finishRename();
    if (e.key === "Escape") { setEditingId(null); setEditTitle(""); }
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo">
          RAG<span>Agent</span>
        </div>
        <button className="sidebar-new-btn" onClick={handleNew}>
          <PlusIcon size={12} /> 新对话
        </button>
      </div>

      <nav className="sidebar-nav">
        <Link to="/" className={location.pathname === "/" ? "active" : ""}>
          <ChatIcon /> 对话
        </Link>
        <Link to="/documents" className={location.pathname === "/documents" ? "active" : ""}>
          <DocIcon /> 知识库
        </Link>
        <Link to="/settings" className={location.pathname === "/settings" ? "active" : ""}>
          <SettingsIcon /> 设置
        </Link>
        <Link to="/memories" className={location.pathname === "/memories" ? "active" : ""}>
          <BrainIcon /> 记忆
        </Link>
      </nav>

      <div className="sidebar-conv-label">
        历史对话
        {conversations.length > 0 && (
          <button className="sidebar-clear-all" onClick={handleClearAll}>
            一键清除
          </button>
        )}
      </div>

      <div className="sidebar-convs">
        {conversations.length === 0 && (
          <p style={{ padding: "8px 12px", fontSize: 12, color: "var(--muted)" }}>暂无对话</p>
        )}
        {conversations.map((conv) => (
          <div
            key={conv.id}
            className={`sidebar-conv ${conv.id === currentConvId ? "active" : ""}`}
            onClick={() => { switchConversation(conv.id); navigate("/"); }}
          >
            {editingId === conv.id ? (
              <input
                ref={editRef}
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onBlur={finishRename}
                onKeyDown={handleRenameKey}
                onClick={(e) => e.stopPropagation()}
                style={{
                  flex: 1,
                  background: "var(--accent-dim)",
                  border: "1px solid var(--accent)",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--fg)",
                  fontSize: 13,
                  padding: "2px 6px",
                  outline: "none",
                }}
              />
            ) : (
              <span
                className="sidebar-conv-title"
                title={conv.title + (conv.id === currentConvId ? "（双击重命名）" : "（双击重命名）")}
                onDoubleClick={(e) => startRename(e, conv.id, conv.title)}
              >
                {conv.title}
              </span>
            )}
            <button
              className="sidebar-conv-del"
              onClick={(e) => handleDelete(e, conv.id, conv.title)}
              title="删除"
            >
              <TrashIcon size={12} />
            </button>
          </div>
        ))}
      </div>
    </aside>
  );
}
