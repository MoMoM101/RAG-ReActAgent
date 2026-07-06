import { create } from "zustand";

export interface Toast {
  id: string;
  type: "success" | "error" | "info" | "warning";
  message: string;
  removing?: boolean;
}

interface ToastStore {
  toasts: Toast[];
  addToast: (t: Omit<Toast, "id">) => void;
  removeToast: (id: string) => void;
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],

  addToast: (t) => {
    const id = crypto.randomUUID();
    set((s) => ({ toasts: [...s.toasts, { ...t, id }] }));
    setTimeout(() => {
      set((s) => ({
        toasts: s.toasts.map((x) => (x.id === id ? { ...x, removing: true } : x)),
      }));
      setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) }));
      }, 200);
    }, t.type === "error" ? 5000 : 3000);
  },

  removeToast: (id) => {
    set((s) => ({
      toasts: s.toasts.map((x) => (x.id === id ? { ...x, removing: true } : x)),
    }));
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) }));
    }, 200);
  },
}));
