import { createContext, useContext } from "react";

export interface ConfirmOptions {
  title: string;
  message: string;
  variant?: "default" | "danger";
  confirmLabel?: string;
  cancelLabel?: string;
}

export const ConfirmCtx = createContext<(opts: ConfirmOptions) => Promise<boolean>>(
  () => Promise.resolve(false),
);

export function useConfirm() {
  return useContext(ConfirmCtx);
}
