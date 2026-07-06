import { useChatStore } from "../../stores/chatStore";
import { MessageBubble } from "./MessageBubble";
import { useEffect, useRef, useState, useCallback } from "react";
import { BrainIcon, ArrowDownIcon } from "../shared/Icons";
import { Skeleton } from "../shared/Skeleton";

export function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const sseState = useChatStore((s) => s.sseState);
  const loadingHistory = useChatStore((s) => s.loadingHistory);
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const userScrolledUp = useRef(false);

  const scrollToBottom = useCallback((smooth = true) => {
    bottomRef.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto" });
    userScrolledUp.current = false;
    setShowScrollBtn(false);
  }, []);

  // Auto-scroll on new messages unless user scrolled up
  useEffect(() => {
    if (!userScrolledUp.current) {
      scrollToBottom(true);
    }
  }, [messages, scrollToBottom]);

  // Auto-scroll when streaming starts
  useEffect(() => {
    if (sseState === "connecting" || sseState === "streaming") {
      userScrolledUp.current = false;
      scrollToBottom(true);
    }
  }, [sseState, scrollToBottom]);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom > 200) {
      userScrolledUp.current = true;
      setShowScrollBtn(true);
    } else {
      userScrolledUp.current = false;
      setShowScrollBtn(false);
    }
  }, []);

  // Scroll to bottom on initial load
  useEffect(() => {
    if (messages.length > 0) {
      scrollToBottom(false);
    }
  }, [loadingHistory]); // eslint-disable-line

  if (messages.length === 0 && !loadingHistory) {
    return <ChatEmpty />;
  }

  return (
    <>
      <div ref={containerRef} className="chat-messages" onScroll={handleScroll}>
        {loadingHistory && (
          <div style={{ padding: 20 }}>
            <Skeleton height={40} count={3} />
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        {/* Typing indicator */}
        {(sseState === "connecting" || sseState === "streaming") && (
          <div className="typing-indicator">
            <div className="typing-dots"><span /><span /><span /></div>
            <span>
              {sseState === "connecting" ? "连接中..." : "思考中..."}
            </span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {showScrollBtn && (
        <button className="scroll-bottom-btn" onClick={() => scrollToBottom(true)}>
          <ArrowDownIcon size={16} />
        </button>
      )}
    </>
  );
}

function ChatEmpty() {
  const send = useChatStore((s) => s.send);

  const suggestions = [
    "总结已上传的文档内容",
    "对比文档 A 和文档 B 的核心观点",
    "从文档中提取所有关键数据",
  ];

  return (
    <div className="chat-empty">
      <BrainIcon size={36} style={{ color: "var(--muted)", marginBottom: 4 }} />
      <h2>RAG Agent 智能文档助手</h2>
      <p>上传文档后即可开始提问。Agent 会自动检索相关内容并给出带引用的回答。</p>
      <div className="suggs">
        {suggestions.map((s) => (
          <span key={s} className="sugg-chip" onClick={() => send(s)}>{s}</span>
        ))}
      </div>
    </div>
  );
}
