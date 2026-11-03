import type { ReviewReason } from "./api/types";

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU");
}

export const REVIEW_REASONS: Record<ReviewReason, string> = {
  no_sources: "Не найдено подходящих источников в базе знаний",
  low_confidence: "Низкая уверенность модели в ответе",
  unverified_quote: "Цитата не найдена дословно в документе",
  llm_invalid_json: "Модель вернула ответ в некорректном формате",
  llm_error: "Ошибка при обращении к языковой модели",
};

export function reviewReason(code: string | null): string {
  if (!code) return "—";
  return (REVIEW_REASONS as Record<string, string>)[code] ?? `Другая причина (${code})`;
}
