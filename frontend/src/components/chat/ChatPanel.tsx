import { useEffect, useState } from "react";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { useDocumentStore } from "../../stores/documentStore";

export function ChatPanel() {
  const [docCount, setDocCount] = useState<number | null>(null);
  const load = useDocumentStore((s) => s.load);
  const documents = useDocumentStore((s) => s.documents);

  useEffect(() => {
    load().then(() => setDocCount(documents.length));
  }, [load, documents.length]);

  return (
    <div className="chat-main">
      {docCount === 0 && (
        <div style={{
          margin: "0 auto 12px",
          padding: "10px 16px",
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          fontSize: 13,
          color: "var(--muted)",
          textAlign: "center" as const,
          maxWidth: 520,
        }}>
          知识库还没有文档。
          <a href="/documents" style={{ color: "var(--accent)", marginLeft: 6 }}>
            上传文档
          </a>
          ，即可基于文档内容提问并获得带引用的回答。
        </div>
      )}
      <MessageList />
      <ChatInput />
    </div>
  );
}
