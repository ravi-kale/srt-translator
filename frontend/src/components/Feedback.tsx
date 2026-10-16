import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Button, cx } from "./ui";

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------

type ToastTone = "info" | "success" | "warning" | "error";
interface Toast {
  id: number;
  title: string;
  body?: string;
  tone: ToastTone;
}

const ToastContext = createContext<(t: Omit<Toast, "id">) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

const TOAST_ICON: Record<ToastTone, ReactNode> = {
  info: <Info size={16} className="text-accent" />,
  success: <CheckCircle2 size={16} className="text-ok" />,
  warning: <AlertTriangle size={16} className="text-warn" />,
  error: <XCircle size={16} className="text-danger" />,
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);
  const push = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = nextId.current++;
      setToasts((ts) => [...ts.slice(-3), { ...t, id }]);
      window.setTimeout(() => dismiss(id), t.tone === "error" ? 9000 : 5000);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="animate-slide-in pointer-events-auto flex gap-3 rounded-xl border border-line bg-panel p-3 shadow-xl shadow-black/20"
          >
            <span className="mt-0.5 shrink-0">{TOAST_ICON[t.tone]}</span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-ink">{t.title}</div>
              {t.body && <div className="mt-0.5 text-xs text-muted break-words">{t.body}</div>}
            </div>
            <button className="shrink-0 text-muted hover:text-ink" onClick={() => dismiss(t.id)} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Confirm dialog
// ---------------------------------------------------------------------------

interface ConfirmOptions {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  tone?: "primary" | "danger";
}

const ConfirmContext = createContext<(o: ConfirmOptions) => Promise<boolean>>(async () => false);

export function useConfirm() {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOptions & { resolve: (v: boolean) => void }) | null>(null);

  const confirm = useCallback(
    (o: ConfirmOptions) => new Promise<boolean>((resolve) => setState({ ...o, resolve })),
    [],
  );
  const close = (v: boolean) => {
    state?.resolve(v);
    setState(null);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state && (
        <Modal onClose={() => close(false)} labelledBy="confirm-title" width="max-w-md">
          <div className="p-5">
            <h2 id="confirm-title" className="text-base font-semibold text-ink">
              {state.title}
            </h2>
            {state.body && <div className="mt-2 text-sm text-ink-2">{state.body}</div>}
          </div>
          <div className="flex justify-end gap-2 border-t border-line bg-panel-2/50 px-5 py-3">
            <Button variant="ghost" onClick={() => close(false)}>
              Cancel
            </Button>
            <Button variant={state.tone === "danger" ? "danger" : "primary"} onClick={() => close(true)} autoFocus>
              {state.confirmLabel ?? "Confirm"}
            </Button>
          </div>
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Modal shell
// ---------------------------------------------------------------------------

export function Modal({
  children,
  onClose,
  labelledBy,
  width = "max-w-2xl",
}: {
  children: ReactNode;
  onClose: () => void;
  labelledBy: string;
  width?: string;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/55 p-4 backdrop-blur-[2px] sm:items-center" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        className={cx("animate-slide-in my-auto w-full overflow-hidden rounded-2xl border border-line bg-panel shadow-2xl shadow-black/40", width)}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
