// HTTP-клиент API базы знаний: единая обработка ошибок ({error, detail} → ApiError) и
// типизированные методы для публичного API (/kb) и админки (/admin). Компоненты ходят на сервер только через него.
import type {
  AskResponse,
  CreateDocumentResponse,
  DocumentDetail,
  DocumentListItem,
  EmbeddingModel,
  ErrorBody,
  HealthReport,
  FieldError,
  QaRun,
} from "./types";

/** Базовый адрес API (VITE_API_URL при сборке), без завершающих слэшей. */
export const API_URL: string = (
  (import.meta.env.VITE_API_URL as string | undefined) || "http://localhost:8000"
).replace(/\/+$/, "");

/** Ошибка ответа API: HTTP-статус, машинный код (`error`) и `detail` из единого тела ошибки бэкенда. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;
  constructor(status: number, code: string, detail: unknown, message: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
  /** Ошибки 422 в виде {поле: сообщение}. */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    if (Array.isArray(this.detail)) {
      for (const item of this.detail as FieldError[]) {
        if (item && typeof item.field === "string") {
          // Берём последний сегмент пути поля (`body.title` → `title`), сообщения одного поля склеиваем.
          const key = item.field.split(".").pop() ?? item.field;
          out[key] = out[key] ? `${out[key]}; ${item.msg}` : item.msg;
        }
      }
    }
    return out;
  }
}

/** Сервер недоступен (fetch упал до получения ответа: сеть, CORS, адрес). */
export class NetworkError extends Error {}

// Человекочитаемые сообщения для кодов ошибок бэкенда (app/api/errors.py).
const CODE_MESSAGES: Record<string, string> = {
  bad_request: "Некорректный запрос",
  validation_error: "Ошибка валидации",
  not_found: "Не найдено",
  unauthorized: "Неверный админ-токен",
  conflict: "Конфликт состояния",
  file_too_large: "Файл слишком большой",
  unsupported_media_type: "Неподдерживаемый тип файла (нужен .txt/.md в UTF-8, .docx или .pdf)",
  extraction_failed: "Не удалось извлечь текст из файла (для PDF нужен текстовый слой)",
  llm_error: "Ошибка языковой модели",
  llm_invalid_json: "Модель вернула некорректный ответ",
  internal_error: "Внутренняя ошибка сервера",
};

// Текст ошибки для пользователя: сообщение по коду + строковый detail, если он есть.
function describe(code: string, detail: unknown): string {
  const base = CODE_MESSAGES[code] ?? `Ошибка API (${code})`;
  if (typeof detail === "string" && detail) return `${base}: ${detail}`;
  return base;
}

/**
 * Выполняет запрос и разбирает JSON.
 * Неуспешный ответ → ApiError (если тело не JSON — код `http_<статус>`), сбой сети → NetworkError.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new NetworkError(`Сервер недоступен (${API_URL})`);
  }
  if (!resp.ok) {
    let body: ErrorBody | null = null;
    try {
      body = (await resp.json()) as ErrorBody;
    } catch {
      body = null;
    }
    const code = body?.error ?? `http_${resp.status}`;
    throw new ApiError(resp.status, code, body?.detail, describe(code, body?.detail));
  }
  return (await resp.json()) as T;
}

// Параметры POST-запроса с JSON-телом.
function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** Публичное API: документы, вопросы, история. Id всегда строки (как в API). */
export const api = {
  listDocuments: (limit = 200, offset = 0) =>
    request<DocumentListItem[]>(`/kb/documents?limit=${limit}&offset=${offset}`),
  getDocument: (id: string, lines?: number) =>
    request<DocumentDetail>(
      `/kb/documents/${encodeURIComponent(id)}${lines ? `?lines=${lines}` : ""}`,
    ),
  createDocument: (title: string, text: string) =>
    request<CreateDocumentResponse>("/kb/documents", json({ title, text })),
  uploadDocument: (file: File, title?: string) => {
    // multipart: Content-Type с boundary выставит браузер, вручную его не задаём.
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    return request<CreateDocumentResponse>("/kb/documents", { method: "POST", body: form });
  },
  ask: (question: string) => request<AskResponse>("/kb/ask", json({ question })),
  listQaRuns: (opts: { needsReview?: boolean; limit?: number; offset?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.needsReview !== undefined) p.set("needs_review", String(opts.needsReview));
    p.set("limit", String(opts.limit ?? 50));
    if (opts.offset) p.set("offset", String(opts.offset));
    return request<QaRun[]>(`/kb/qa_runs?${p.toString()}`);
  },
  getQaRun: (id: string) => request<QaRun>(`/kb/qa_runs/${encodeURIComponent(id)}`),
};

/** Ключ sessionStorage для админ-токена (живёт только в текущей вкладке). */
export const ADMIN_TOKEN_KEY = "kb.adminToken";

// Параметры запроса к /admin: заголовок X-Admin-Token и JSON-тело при необходимости.
function adminInit(token: string, method = "GET", body?: unknown): RequestInit {
  const headers: Record<string, string> = { "X-Admin-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return { method, headers, body: body === undefined ? undefined : JSON.stringify(body) };
}

// Имя модели может содержать `/` и `:` (например, `qwen2.5:7b`) — кодируем его целиком.
const modelPath = (name: string) => `/admin/embedding-models/${encodeURIComponent(name)}`;

/** Админ-API: health, реестр моделей эмбеддингов, blue/green-переиндексация и активация. */
export const adminApi = {
  health: (t: string) => request<HealthReport>("/admin/health", adminInit(t)),
  listModels: (t: string) => request<EmbeddingModel[]>("/admin/embedding-models", adminInit(t)),
  registerModel: (t: string, m: { provider: string; name: string; dimension: number }) =>
    request<EmbeddingModel>("/admin/embedding-models", adminInit(t, "POST", m)),
  reindex: (t: string, name: string) =>
    request<{ task_id: string; model: EmbeddingModel }>(`${modelPath(name)}/reindex`, adminInit(t, "POST")),
  status: (t: string, name: string) => request<EmbeddingModel>(`${modelPath(name)}/status`, adminInit(t)),
  activate: (t: string, name: string) => request<EmbeddingModel>(`${modelPath(name)}/activate`, adminInit(t, "POST")),
  deleteModel: (t: string, name: string) =>
    request<{ deleted: string; embeddings_deleted: number }>(modelPath(name), adminInit(t, "DELETE")),
};

/** Абсолютная ссылка на скачивание: presigned-URL S3 как есть, относительный путь — от API_URL. */
export function downloadHref(url: string): string {
  return /^https?:\/\//.test(url) ? url : `${API_URL}${url.startsWith("/") ? "" : "/"}${url}`;
}

/** Текст любой ошибки для уведомления. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError || e instanceof NetworkError) return e.message;
  return e instanceof Error ? e.message : String(e);
}
