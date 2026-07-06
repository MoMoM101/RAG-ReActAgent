import { useChatStore } from "../stores/chatStore";

export function useSSE() {
  const sseState = useChatStore((s) => s.sseState);
  const send = useChatStore((s) => s.send);
  const stop = useChatStore((s) => s.stop);
  const error = useChatStore((s) => s.error);
  const clearError = useChatStore((s) => s.clearError);

  return { sseState, send, stop, error, clearError };
}
