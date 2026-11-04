// Форматирование для интерфейса: даты и тексты причин ручной проверки.
import type { ReviewReason } from "./api/types";

/** ISO-дата → локальное время ru-RU; некорректная строка возвращается как есть. */
export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU");
}

/** Тексты причин needs_review (коды — ReviewReason бэкенда). */
export const REVIEW_REASONS: Record<ReviewReason, string> = {
  no_sources: "Не найдено подходящих источников в базе знаний",
  low_confidence: "Низкая уверенность модели в ответе",
  unverified_quote: "Цитата не найдена дословно в документе",
  llm_invalid_json: "Модель вернула ответ в некорректном формате",
  llm_error: "Ошибка при обращении к языковой модели",
};

/** Текст причины по коду; неизвестный код показываем как есть, чтобы не терять информацию. */
export function reviewReason(code: string | null): string {
  if (!code) return "—";
  return (REVIEW_REASONS as Record<string, string>)[code] ?? `Другая причина (${code})`;
}
