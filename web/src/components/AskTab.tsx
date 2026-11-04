// Вкладка «Вопросы»: вопрос к базе знаний (/kb/ask), ответ с источниками и бейджем ручной проверки.
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, api, errorMessage } from "../api/client";
import { LIMITS, type AskResponse } from "../api/types";
import { ReviewBadge } from "./Badges";
import { SourcesList } from "./Sources";
import { useToast } from "./Toasts";

/** Клиентская проверка вопроса (те же лимиты, что на сервере). Возвращает текст ошибки или undefined. */
export function validateQuestion(q: string): string | undefined {
  const t = q.trim();
  if (t.length < LIMITS.questionMin) return `Вопрос должен быть не короче ${LIMITS.questionMin} символов`;
  if (q.length > LIMITS.questionMax) return `Не более ${LIMITS.questionMax} символов`;
  return undefined;
}

/** Форма вопроса и ответ. Отправка — кнопкой или Ctrl/Cmd+Enter. */
export function AskTab() {
  const toast = useToast();
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);

  const ask = async () => {
    // Повторная отправка во время запроса игнорируется.
    if (loading) return;
    const v = validateQuestion(question);
    setError(v);
    if (v) return;
    setLoading(true);
    try {
      setResult(await api.ask(question));
    } catch (e) {
      // 422 — ошибка валидации на сервере: показываем у поля, остальное — уведомлением.
      if (e instanceof ApiError && e.status === 422) setError(e.fieldErrors().question ?? e.message);
      else toast(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void ask();
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void ask();
    }
  };

  return (
    <div className="stack">
      <form className="card" onSubmit={onSubmit} noValidate aria-label="Задать вопрос">
        <label htmlFor="question">Вопрос</label>
        <textarea
          id="question"
          rows={4}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKey}
          aria-invalid={!!error}
          aria-describedby="question-hint"
        />
        <p id="question-hint" className="muted small">Ctrl/Cmd + Enter — отправить</p>
        {error ? <p className="field-error">{error}</p> : null}
        <button type="submit" disabled={loading} aria-busy={loading}>
          {loading ? "Ищем ответ…" : "Спросить"}
        </button>
      </form>
      {result ? (
        <section className="card" aria-label="Ответ" aria-live="polite">
          <div className="row between">
            <h2>Ответ</h2>
            {result.needs_review ? <ReviewBadge /> : null}
          </div>
          <p className="answer">{result.answer}</p>
          <h3>Источники</h3>
          <SourcesList sources={result.sources} />
        </section>
      ) : null}
    </div>
  );
}
