import { create } from "zustand";
import type { DisplayMessage, AgentStep, GroundingVerification, SourceReference } from "../types/chat";
import type { SSEState } from "../types";
import { sendMessage } from "../api/chat";
import { listConversations, createConversation, deleteConversation, deleteAllConversations, getMessages, renameConversation } from "../api/conversations";

interface ChatStore {
  messages: DisplayMessage[];
  conversations: Array<{ id: string; title: string; updated_at: string }>;
  currentConvId: string | null;
  sseState: SSEState;
  error: string | null;
  abortController: AbortController | null;
  loadingHistory: boolean;

  loadConversations: () => Promise<void>;
  newConversation: () => Promise<void>;
  switchConversation: (id: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  deleteAllConversations: () => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  send: (text: string) => Promise<void>;
  stop: () => void;
  clearError: () => void;
}

const EMPTY_ANSWER_FALLBACK = "抱歉，本次未收到有效回答，请重试或换一种问法。";
const INTERRUPTED_ANSWER_FALLBACK = "回答连接已中断，未收到有效正文，请重试。";

export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  conversations: [],
  currentConvId: null,
  sseState: "idle",
  error: null,
  abortController: null,
  loadingHistory: false,

  loadConversations: async () => {
    try {
      const convs = await listConversations();
      set({ conversations: convs });
    } catch { /* ignore — backend may not be running */ }
  },

  newConversation: async () => {
    try {
      const conv = await createConversation();
      set({
        conversations: [conv, ...get().conversations],
        currentConvId: conv.id,
        messages: [],
        error: null,
      });
    } catch {
      // fallback: local-only
      const fallbackId = crypto.randomUUID();
      set({ currentConvId: fallbackId, messages: [], error: null });
    }
  },

  switchConversation: async (id: string) => {
    set({ currentConvId: id, loadingHistory: true, error: null });
    try {
      const msgs = await getMessages(id);
      const displayMsgs: DisplayMessage[] = [];

      for (const m of (msgs as Array<{
        id: string; role: string; content: string | null;
        tool_name?: string; tool_call_id?: string; tool_args?: string;
        tool_result_summary?: {
          kind?: string; count?: number | null; name?: string; value?: string | number;
        } | null;
        sources?: string; verification?: string; created_at: string;
      }>)) {
        if (m.role === "tool") {
          const content = m.content || "";
          let legacyResultCount: number | undefined;
          let success = true;
          const match = content.match(/Success:\s*(\d+)\s*results?/);
          if (match) {
            legacyResultCount = parseInt(match[1], 10);
          } else if (content.startsWith("Error:")) {
            success = false;
          }

          let args: Record<string, unknown> = {};
          if (m.tool_args) {
            try { args = JSON.parse(m.tool_args); } catch { /* ignore */ }
          }

          const toolName = m.tool_name || "unknown";

          // Attach steps to the most recent assistant message
          for (let i = displayMsgs.length - 1; i >= 0; i--) {
            if (displayMsgs[i].role === "assistant") {
              // Tool-calling preambles are protocol context, not final answer text.
              // Hide them just as the live SSE path does when the first tool starts.
              displayMsgs[i].content = "";
              displayMsgs[i].steps.push({
                type: "tool_call",
                data: { tool: toolName, args, call_id: m.tool_call_id },
                timestamp: Date.now(),
              });
              displayMsgs[i].steps.push({
                type: "tool_result",
                data: {
                  tool: toolName,
                  success,
                  result_count: m.tool_result_summary?.count ?? legacyResultCount,
                  result_kind: m.tool_result_summary?.kind,
                  result_name: m.tool_result_summary?.name,
                  result_value: m.tool_result_summary?.value,
                  reranked: false,
                },
                timestamp: Date.now(),
              });
              break;
            }
          }
        } else {
          displayMsgs.push({
            id: m.id,
            role: m.role as DisplayMessage["role"],
            content: m.content || "",
            steps: [],
            sources: m.sources ? JSON.parse(m.sources) : undefined,
            verification: m.verification ? JSON.parse(m.verification) : undefined,
            isStreaming: false,
          });
        }
      }

      set({ messages: displayMsgs, loadingHistory: false });
    } catch {
      set({ messages: [], loadingHistory: false });
    }
  },

  deleteConversation: async (id: string) => {
    try { await deleteConversation(id); } catch { /* ignore */ }
    set((s) => {
      const convs = s.conversations.filter((c) => c.id !== id);
      if (s.currentConvId === id) {
        return { conversations: convs, currentConvId: null, messages: [] };
      }
      return { conversations: convs };
    });
  },

  deleteAllConversations: async () => {
    try { await deleteAllConversations(); } catch { /* ignore */ }
    set({ conversations: [], currentConvId: null, messages: [] });
  },

  renameConversation: async (id: string, title: string) => {
    try { await renameConversation(id, title); } catch { /* ignore */ }
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title } : c
      ),
    }));
  },

  send: async (text: string) => {
    const msgId = crypto.randomUUID();
    const userMsg: DisplayMessage = {
      id: msgId, role: "user", content: text, steps: [], isStreaming: false,
    };
    const requestStart = Date.now();
    const assistantMsg: DisplayMessage = {
      id: crypto.randomUUID(), role: "assistant", content: "", steps: [], isStreaming: true,
      startTime: requestStart,
    };

    // Auto-create conversation if none active
    const convId = get().currentConvId;
    const isNewConv = !convId;

    set((s) => ({
      messages: [...s.messages, userMsg, assistantMsg],
      sseState: "connecting",
      error: null,
    }));

    const controller = sendMessage(
      text,
      convId,
      (event) => {
        set((s) => {
          const msgs = [...s.messages];
          const last = msgs[msgs.length - 1];
          if (last.role !== "assistant") return s;

          const step: AgentStep = {
            type: event.event as AgentStep["type"],
            data: event.data as Record<string, unknown>,
            timestamp: Date.now(),
          };

          if (event.event === "thought") {
            last.thought = (last.thought || "") + ((event.data as { delta: string }).delta || "");
          }
          if (event.event === "answer_chunk") {
            last.content += (event.data as { delta: string }).delta || "";
          }
          if (event.event === "answer_replace") {
            last.content = (event.data as { content: string }).content || "";
          }
          if (
            event.event === "tool_call"
            && !last.steps.some((existingStep) => existingStep.type === "tool_call")
          ) {
            // The backend resets pre-tool assistant text into protocol history.
            // Mirror that reset so narration such as "先搜索一下" cannot be
            // glued to the final Markdown answer in the live message bubble.
            last.content = "";
          }
          if (event.event === "sources") {
            last.sources = event.data as SourceReference[];
          }
          if (event.event === "verification") {
            last.verification = event.data as unknown as GroundingVerification;
          }
          if (event.event === "done" || event.event === "error") {
            if (!last.content.trim()) {
              last.content = event.event === "error"
                ? INTERRUPTED_ANSWER_FALLBACK
                : EMPTY_ANSWER_FALLBACK;
            }
            last.isStreaming = false;
            last.duration = Date.now() - (last.startTime || Date.now());
          }
          last.steps.push(step);
          msgs[msgs.length - 1] = { ...last };

          const newSseState: SSEState =
            event.event === "done" ? "idle"
            : event.event === "error" ? "error"
            : "streaming";

          const errData = event.event === "error"
            ? (event.data as { message?: string; code?: string })
            : null;
          const errorMsg = errData
            ? (errData.message || `请求中断 (${errData.code})`)
            : null;

          return { messages: msgs, sseState: newSseState, abortController: null, error: errorMsg };
        });
      },
      (err) => {
        set((s) => {
          const msgs = [...s.messages];
          const last = msgs[msgs.length - 1];
          if (last.role === "assistant") {
            if (!last.content.trim()) {
              last.content = INTERRUPTED_ANSWER_FALLBACK;
            }
            last.isStreaming = false;
            last.duration = Date.now() - (last.startTime || Date.now());
            msgs[msgs.length - 1] = { ...last };
          }
          return {
            messages: msgs,
            sseState: "error",
            error: `连接失败: ${err.message || "网络错误"}`,
            abortController: null,
          };
        });
      },
      () => {
        set((s) => {
          if (s.sseState === "error") return s;
          const msgs = [...s.messages];
          const last = msgs[msgs.length - 1];
          if (last.role === "assistant") {
            if (!last.content.trim()) {
              last.content = EMPTY_ANSWER_FALLBACK;
            }
            last.isStreaming = false;
            if (!last.duration) {
              last.duration = Date.now() - (last.startTime || Date.now());
            }
            msgs[msgs.length - 1] = { ...last };
          }
          return { messages: msgs, sseState: "idle", abortController: null };
        });
      },
      (newConvId) => {
        // Auto-add new conversation to sidebar on first message
        if (isNewConv) {
          const title = text.length > 40 ? text.slice(0, 40) + "..." : text;
          set((s) => ({
            currentConvId: newConvId,
            conversations: [
              { id: newConvId, title, updated_at: new Date().toISOString() },
              ...s.conversations,
            ],
          }));
        }
      },
    );

    set({ abortController: controller });
  },

  stop: () => {
    get().abortController?.abort();
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last.role === "assistant") {
        last.isStreaming = false;
        last.duration = Date.now() - (last.startTime || Date.now());
        msgs[msgs.length - 1] = { ...last };
      }
      return { messages: msgs, sseState: "idle", abortController: null, error: null };
    });
  },

  clearError: () => set({ error: null }),
}));
