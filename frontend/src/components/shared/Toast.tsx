import { useToastStore } from "../../stores/toastStore";
import { createPortal } from "react-dom";

export function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts);
  const remove = useToastStore((s) => s.removeToast);

  if (toasts.length === 0) return null;

  return createPortal(
    <div className="toast-container">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`toast ${t.type} ${t.removing ? "removing" : ""}`}
          onClick={() => remove(t.id)}
        >
          {t.message}
        </div>
      ))}
    </div>,
    document.body,
  );
}
