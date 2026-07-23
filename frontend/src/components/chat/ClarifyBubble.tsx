import { WarnIcon } from "../shared/Icons";

export function ClarifyBubble({ question }: { question: string }) {
  return (
    <div className="clarify-bubble">
      <WarnIcon size={14} style={{ flexShrink: 0, marginTop: 1 }} />
      <span>{question}</span>
    </div>
  );
}
