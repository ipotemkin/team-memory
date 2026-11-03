import type { DocumentStatus } from "../api/types";

const LABELS: Record<DocumentStatus, string> = {
  indexing: "индексируется",
  ready: "готов",
  failed: "ошибка",
};

export function StatusBadge({ status, error }: { status: DocumentStatus; error?: string | null }) {
  const title = status === "failed" && error ? error : undefined;
  return (
    <span className={`badge status-${status}`} title={title} data-testid="status-badge">
      {LABELS[status] ?? status}
      {title ? <span className="sr-only">: {title}</span> : null}
    </span>
  );
}

export function ReviewBadge() {
  return <span className="badge review">требует проверки</span>;
}
