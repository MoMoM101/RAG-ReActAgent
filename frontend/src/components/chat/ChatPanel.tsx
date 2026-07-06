import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";

export function ChatPanel() {
  return (
    <div className="chat-main">
      <MessageList />
      <ChatInput />
    </div>
  );
}
