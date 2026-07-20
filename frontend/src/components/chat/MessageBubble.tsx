import { useState, useCallback } from "react";
import type { DisplayMessage } from "../../types/chat";
import { ToolCallCard } from "./ToolCallCard";
import { SourceCard } from "./SourceCard";
import { ClarifyBubble } from "./ClarifyBubble";
import { CopyIcon, CheckIcon, ThumbUpIcon, ThumbDownIcon } from "../shared/Icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function CodeBlock({ children, className }: { children?: React.ReactNode; className?: string }) {
  const [copied, setCopied] = useState(false);
  const match = /language-(\w+)/.exec(className || "");
  const lang = match ? match[1] : "";
  const code = String(children).replace(/\n$/, "");

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [code]);

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="code-block-lang">{lang || "code"}</span>
        <button className={`copy-btn ${copied ? "copied" : ""}`} onClick={handleCopy}>
          {copied ? <><CheckIcon size={10} /> 已复制</> : <><CopyIcon size={10} /> 复制</>}
        </button>
      </div>
      <pre><code className={className}>{children}</code></pre>
    </div>
  );
}

interface Props { message: DisplayMessage }

function formatDuration(ms: number): string {
  if (ms < 1000) return "<1s";
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const mins = Math.floor(sec / 60);
  const remain = Math.floor(sec % 60);
  return `${mins}m ${remain}s`;
}

function CopyTextBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handle = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);
  return (
    <button className="feedback-btn" onClick={handle} title="复制全文" style={{ marginLeft: "auto" }}>
      {copied ? <><CheckIcon size={12} /> 已复制</> : <><CopyIcon size={12} /> 复制</>}
    </button>
  );
}

export function MessageBubble({ message }: Props) {
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);

  if (message.role === "user") {
    return (
      <div className="msg user">
        <div className="msg-avatar">U</div>
        <div className="msg-body">
          <span className="msg-role">你</span>
          <div className="msg-content">{message.content}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="msg agent">
      <div className="msg-avatar">AI</div>
      <div className="msg-body">
        <span className="msg-role">RAG Agent</span>

        {/* Tool calls & clarifications before answer */}
        {message.steps.map((step, i) => {
          if (step.type === "tool_call" || step.type === "tool_result") {
            return <ToolCallCard key={i} step={step} />;
          }
          if (step.type === "clarification") {
            return <ClarifyBubble key={i} question={(step.data as { question: string }).question} />;
          }
          return null;
        })}

        {/* Answer content */}
        {message.thought && (
          <div className="msg-thought prose">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.thought}
            </ReactMarkdown>
          </div>
        )}
        {message.content && (
          <div className="msg-content prose">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                pre: ({ children }) => <>{children}</>,
                code: ({ className, children, ...props }) => {
                  const isInline = !className;
                  if (isInline) {
                    return <code className={className} {...props}>{children}</code>;
                  }
                  return <CodeBlock className={className}>{children}</CodeBlock>;
                },
              }}
            >
              {message.content}
            </ReactMarkdown>
            {/* Streaming cursor */}
            {message.isStreaming && (
              <span style={{ display: "inline-block", width: 8, height: 16, background: "var(--accent)", borderRadius: 2, marginLeft: 2, verticalAlign: "text-bottom" }} />
            )}
          </div>
        )}

        {/* Sources */}
        {message.sources && message.sources.length > 0 && (
          <SourceCard sources={message.sources} />
        )}

        {message.verification && (
          message.verification.display_status === "verified"
          || message.verification.display_status === "warning"
          || (!message.verification.display_status && (
            message.verification.status === "verified"
            || (message.verification.unsupported_claims?.length ?? 0) > 0
          ))
        ) && (
          <div
            title={`引用精确率 ${(message.verification.citation_precision * 100).toFixed(0)}%，引用完整率 ${(message.verification.citation_recall * 100).toFixed(0)}%`}
            style={{
              marginTop: 6,
              fontSize: 11,
              color: message.verification.display_status === "warning" ? "var(--warning, #b45309)" : "var(--success, #16a34a)",
            }}
          >
            {message.verification.display_status === "warning" ? "⚠ 部分事实缺少来源支持" : "✓ 来源验证通过"}
            {` · 忠实度 ${(message.verification.faithfulness * 100).toFixed(0)}%`}
          </div>
        )}

        {/* Feedback + Copy */}
        {!message.isStreaming && message.content && (
          <div className="feedback-btns">
            <button
              className={`feedback-btn ${feedback === "up" ? "active positive" : ""}`}
              onClick={() => setFeedback(feedback === "up" ? null : "up")}
              title="有帮助"
            >
              <ThumbUpIcon size={12} />
            </button>
            <button
              className={`feedback-btn ${feedback === "down" ? "active negative" : ""}`}
              onClick={() => setFeedback(feedback === "down" ? null : "down")}
              title="没帮助"
            >
              <ThumbDownIcon size={12} />
            </button>
            <CopyTextBtn text={message.content} />
          </div>
        )}

        {/* Duration */}
        {!message.isStreaming && message.duration != null && (
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
            耗时 {formatDuration(message.duration)}
          </div>
        )}
      </div>
    </div>
  );
}
