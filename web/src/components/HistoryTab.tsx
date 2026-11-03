import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../api/client";
import type { QaRun } from "../api/types";
import { formatDate, reviewReason } from "../format";
import { hrefs, navigate } from "../router";
import { ReviewBadge } from "./Badges";
import { Modal } from "./Modal";
import { SourcesList } from "./Sources";
import { useToast } from "./Toasts";

export function RunCard({ id, cached, onClose }: { id: string; cached?: QaRun; onClose: () => void }) {
  const toast = useToast();
  const [run, setRun] = useState<QaRun | null>(cached ?? null);
  useEffect(() => {
    if (cached) return;
    api.getQaRun(id).then(setRun).catch((e: unknown) => toast(errorMessage(e)));
  }, [id, cached, toast]);
  return (
    <Modal title={`Запрос #${id}`} onClose={onClose}>
      {!run ? (
        <p aria-busy="true">Загрузка…</p>
      ) : (
        <div className="stack">
          <p><strong>Вопрос:</strong> {run.question}</p>
          <div className="row between">
            <h3>Ответ</h3>
            {run.needs_review ? <ReviewBadge /> : null}
          </div>
          <p className="answer">{run.answer}</p>
          <h3>Источники</h3>
          <SourcesList sources={run.sources} />
          <dl className="meta">
            <dt>Создан</dt><dd>{formatDate(run.created_at)}</dd>
            {run.needs_review ? (<><dt>Причина проверки</dt><dd>{reviewReason(run.error)}</dd></>) : null}
            <dt>Уверенность</dt><dd>{run.confidence == null ? "—" : `${Math.round(run.confidence * 100)}%`}</dd>
            <dt>Модель ответа</dt><dd>{run.chat_model ?? "—"}</dd>
            <dt>Модель эмбеддингов</dt><dd>{run.embedding_model ?? "—"}</dd>
            <dt>Режим поиска</dt><dd>{run.retrieval_mode ?? "—"}</dd>
          </dl>
        </div>
      )}
    </Modal>
  );
}

export function HistoryTab({ runId, active }: { runId: string | null; active: boolean }) {
  const toast = useToast();
  const [runs, setRuns] = useState<QaRun[] | null>(null);
  const [onlyReview, setOnlyReview] = useState(false);
  const [limit, setLimit] = useState(50);

  const load = useCallback(async () => {
    try {
      setRuns(await api.listQaRuns({ needsReview: onlyReview ? true : undefined, limit }));
    } catch (e) {
      toast(errorMessage(e));
    }
  }, [onlyReview, limit, toast]);

  // грузим при открытии вкладки и при смене фильтра/лимита
  useEffect(() => {
    if (active) void load();
  }, [load, active]);

  return (
    <section className="card">
      <div className="row between">
        <h2>История вопросов</h2>
        <div className="row">
          <label className="inline">
            <input type="checkbox" checked={onlyReview} onChange={(e) => setOnlyReview(e.target.checked)} />
            только «требует проверки»
          </label>
          <label className="inline">
            Показать
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="Количество записей">
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </label>
          <button type="button" className="ghost" onClick={() => void load()}>Обновить</button>
        </div>
      </div>
      {runs === null ? (
        <p aria-busy="true">Загрузка…</p>
      ) : runs.length === 0 ? (
        <p className="muted">Записей нет.</p>
      ) : (
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Время</th>
              <th scope="col">Вопрос</th>
              <th scope="col">Нужна проверка</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr
                key={r.id}
                className="clickable"
                tabIndex={0}
                aria-label={`Открыть запрос: ${r.question}`}
                onClick={() => navigate(hrefs.run(r.id))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    navigate(hrefs.run(r.id));
                  }
                }}
              >
                <td>{formatDate(r.created_at)}</td>
                <td>{r.question}</td>
                <td>{r.needs_review ? <ReviewBadge /> : <span className="muted">нет</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {runId ? (
        <RunCard
          id={runId}
          cached={runs?.find((r) => r.id === runId)}
          onClose={() => navigate(hrefs.history)}
        />
      ) : null}
    </section>
  );
}
