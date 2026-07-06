import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface ConfirmOptions {
  title: string;
  message: string;
  variant?: "default" | "danger";
  confirmLabel?: string;
  cancelLabel?: string;
}

interface ConfirmState extends ConfirmOptions {
  id: number;
  resolve: (ok: boolean) => void;
}

let nextId = 0;

const ConfirmCtx = createContext<(opts: ConfirmOptions) => Promise<boolean>>(
  () => Promise.resolve(false),
);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [stack, setStack] = useState<ConfirmState[]>([]);

  const confirm = useCallback((opts: ConfirmOptions): Promise<boolean> => {
    return new Promise((resolve) => {
      setStack((s) => [...s, { ...opts, id: ++nextId, resolve }]);
    });
  }, []);

  const dismiss = (id: number, ok: boolean) => {
    setStack((s) => s.filter((x) => x.id !== id));
    stack.find((x) => x.id === id)?.resolve(ok);
  };

  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      {stack.length > 0 &&
        createPortal(
          <div className="modal-overlay confirm-dialog">
            <div className="modal">
              <div className="confirm-body">
                <h3>{stack[stack.length - 1].title}</h3>
                <p>{stack[stack.length - 1].message}</p>
              </div>
              <div className="confirm-footer">
                <button
                  className="confirm-cancel"
                  onClick={() => dismiss(stack[stack.length - 1].id, false)}
                >
                  {stack[stack.length - 1].cancelLabel || "取消"}
                </button>
                <button
                  className={
                    stack[stack.length - 1].variant === "danger"
                      ? "confirm-danger"
                      : "confirm-primary"
                  }
                  onClick={() => dismiss(stack[stack.length - 1].id, true)}
                >
                  {stack[stack.length - 1].confirmLabel || "确认"}
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </ConfirmCtx.Provider>
  );
}

export function useConfirm() {
  return useContext(ConfirmCtx);
}
