// Уведомления (toasts): провайдер контекста и хук useToast; автоскрытие через 6 секунд.
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

interface Toast {
  id: number;
  text: string;
  kind: "error" | "info";
}
type Push = (text: string, kind?: Toast["kind"]) => void;

const Ctx = createContext<Push>(() => undefined);
// Счётчик id уведомлений (уникален в пределах страницы).
let seq = 0;

/** Хранит уведомления и выводит их; ошибки — role=alert, информационные — role=status. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback<Push>(
    (text, kind = "error") => {
      const id = ++seq;
      setToasts((t) => [...t, { id, text, kind }]);
      window.setTimeout(() => dismiss(id), 6000);
    },
    [dismiss],
  );
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="assertive">
        {toasts.map((t) => (
          <div key={t.id} role={t.kind === "error" ? "alert" : "status"} className={`toast ${t.kind}`}>
            <span>{t.text}</span>
            <button type="button" aria-label="Закрыть уведомление" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

/** Функция показа уведомления: toast(текст, "error" | "info"). */
export function useToast(): Push {
  return useContext(Ctx);
}
