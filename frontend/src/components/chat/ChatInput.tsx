import { useState, useRef, useEffect, type KeyboardEvent } from "react";
import { useChatStore } from "../../stores/chatStore";
import { SendIcon, RefreshIcon } from "../shared/Icons";

const MAX_TOKENS = 128000;
const BUDGET = MAX_TOKENS * 0.8;  // 80% 预算
const MAX_MESSAGE_LENGTH = 4000;

function estimateTokens(text: string | null): number {
  if (!text) return 0;
  return Math.max(1, Math.floor(text.length / 2));
}

export function ChatInput() {
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const { send, stop, sseState, error, clearError, messages } = useChatStore();
  const isDisabled = sseState === "connecting" || sseState === "streaming";

  // 估算当前上下文 token 用量
  const usedTokens = messages.reduce((sum, m) => sum + estimateTokens(m.content || ""), 0);
  const pct = Math.min(100, Math.round((usedTokens / BUDGET) * 100));
  const barColor = pct > 90 ? "var(--danger)" : pct > 70 ? "var(--warn, #f59e0b)" : "var(--muted)";

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 100) + "px";
  }, [text]);

  // Focus input on mount and after state changes
  useEffect(() => {
    if (sseState === "idle") {
      inputRef.current?.focus();
    }
  }, [sseState]);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || isDisabled) return;
    send(trimmed);
    setText("");
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="chat-input-area">
      {/* Error bar */}
      {error && (
        <div className="chat-error-bar">
          <span>{error}</span>
          <button onClick={() => { clearError(); }}>关闭</button>
          <button onClick={() => {
            clearError();
            // Re-send last user message if available
            const msgs = useChatStore.getState().messages;
            const lastUser = [...msgs].reverse().find((m) => m.role === "user");
            if (lastUser) {
              const content = lastUser.content;
              useChatStore.getState().send(content);
            }
          }}>
            <RefreshIcon size={11} /> 重试
          </button>
        </div>
      )}

      <div className="chat-input-wrap">
        <textarea
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入问题，Agent 将检索知识库并回答... (Enter 发送)"
          rows={1}
          maxLength={MAX_MESSAGE_LENGTH}
          disabled={isDisabled}
          className="chat-textarea"
        />

        {isDisabled ? (
          <button className="chat-stop-btn" onClick={stop}>
            停止
          </button>
        ) : (
          <button
            className="chat-send-btn"
            onClick={handleSend}
            disabled={!text.trim()}
          >
            <SendIcon size={14} /> 发送
          </button>
        )}
      </div>

      {/* Token 用量条 */}
      <div className="chat-meta-bar">
        <span className="chat-meta-label">消息</span>
        <span className={`chat-meta-value${text.length > MAX_MESSAGE_LENGTH * 0.9 ? " warn" : ""}`}>
          {text.length}
        </span>
        <span className="chat-meta-label">/ {MAX_MESSAGE_LENGTH.toLocaleString()}</span>
        <span className="chat-meta-sep" />
        <span className="chat-meta-label">上下文</span>
        <div className="chat-meta-gauge">
          <div
            className="chat-meta-gauge-fill"
            style={{ width: `${pct}%`, background: barColor }}
          />
        </div>
        <span className="chat-meta-value">{usedTokens.toLocaleString()}</span>
        <span className="chat-meta-label">/ {Math.round(BUDGET).toLocaleString()}</span>
      </div>
    </div>
  );
}
