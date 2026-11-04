// Модальная боковая панель: закрытие по Esc и клику по фону, фокус внутрь и обратно.
import { useEffect, useRef, type ReactNode } from "react";

/** Диалог с заголовком; при закрытии фокус возвращается на элемент, открывший его. */
export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="backdrop" onClick={onClose}>
      <div
        className="panel"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
        // Клик внутри панели не должен закрывать её (не всплывает до фона).
        onClick={(e) => e.stopPropagation()}
      >
        <header className="panel-head">
          <h2>{title}</h2>
          <button type="button" className="ghost" aria-label="Закрыть" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="panel-body">{children}</div>
      </div>
    </div>
  );
}
