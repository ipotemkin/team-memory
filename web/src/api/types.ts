// Типы вручную по схемам backend/app/api/schemas.py (DTO HTTP API). Все id — строки, даты — ISO 8601.
export type DocumentStatus = "indexing" | "ready" | "failed";

export interface DocumentListItem {
  id: string;
  title: string;
  created_at: string;
  status: DocumentStatus;
  status_error: string | null;
}

export interface DocumentDetail extends DocumentListItem {
  text: string;
  lines_total: number;
  lines_returned: number;
  mime_type: string | null;
  size_bytes: number | null;
  sha256: string | null;
  download_url: string | null;
  snippets_count: number;
}

export interface CreateDocumentResponse {
  status: "ok";
  document_id: string;
  document_status: DocumentStatus;
}

export interface Source {
  document_id: string;
  quote: string;
}

export interface AskResponse {
  answer: string;
  sources: Source[];
  needs_review: boolean;
}

/** Причина ручной проверки (qa_runs.error); тексты — в format.ts. */
export type ReviewReason =
  | "no_sources"
  | "low_confidence"
  | "unverified_quote"
  | "llm_invalid_json"
  | "llm_error";

export interface QaRun {
  id: string;
  created_at: string;
  question: string;
  answer: string;
  sources: Source[];
  needs_review: boolean;
  error: string | null;
  confidence: number | null;
  chat_model: string | null;
  embedding_model: string | null;
  retrieval_mode: string | null;
}

export interface FieldError {
  field: string;
  msg: string;
}

export interface ErrorBody {
  error: string;
  detail?: unknown;
}

/** Клиентские лимиты валидации — те же, что в домене бэкенда (Title, Text, Question, MAX_UPLOAD_MB). */
export const LIMITS = {
  titleMax: 200,
  textMax: 100_000,
  questionMin: 3,
  questionMax: 2000,
  uploadMaxBytes: 20 * 1024 * 1024,
  uploadExtensions: [".txt", ".md", ".docx", ".pdf"],
} as const;

/** Модель из реестра эмбеддингов (/admin); progress — доля переиндексированных фрагментов 0..1. */
export interface EmbeddingModel {
  name: string;
  provider: string;
  dimension: number;
  status: "indexing" | "ready" | "active" | "retired" | "failed";
  progress_done: number;
  progress_total: number;
  progress: number;
  created_at: string;
  activated_at: string | null;
  retired_at: string | null;
  error: string | null;
}

/** Ответ /admin/health: общий статус и состояние каждой зависимости. */
export interface HealthReport {
  status: "ok" | "degraded";
  components: Record<string, { ok: boolean; detail: string }>;
}
