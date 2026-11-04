// Карточка документа (боковая панель): метаданные, превью текста, ссылка на оригинал.
import { useEffect, useState } from "react";
import { api, downloadHref, errorMessage } from "../api/client";
import type { DocumentDetail } from "../api/types";
import { formatDate } from "../format";
import { StatusBadge } from "./Badges";
import { Modal } from "./Modal";

/** Сколько строк текста грузить в превью (параметр ?lines=); «Показать всё» загружает весь текст. */
export const PREVIEW_LINES = 50;

/** Карточка документа по id. */
export function DocumentView({ id, onClose }: { id: string; onClose: () => void }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [all, setAll] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Игнорируем ответ, пришедший после закрытия карточки или смены документа.
    let alive = true;
    setError(null);
    api
      .getDocument(id, all ? undefined : PREVIEW_LINES)
      .then((d) => alive && setDoc(d))
      .catch((e: unknown) => alive && setError(errorMessage(e)));
    return () => {
      alive = false;
    };
  }, [id, all]);

  return (
    <Modal title={doc ? doc.title : `Документ #${id}`} onClose={onClose}>
      {error ? <p role="alert" className="error">{error}</p> : null}
      {!doc && !error ? <p aria-busy="true">Загрузка…</p> : null}
      {doc ? (
        <>
          <dl className="meta">
            <dt>ID</dt>
            <dd>{doc.id}</dd>
            <dt>Создан</dt>
            <dd>{formatDate(doc.created_at)}</dd>
            <dt>Статус</dt>
            <dd>
              <StatusBadge status={doc.status} error={doc.status_error} />
            </dd>
            {doc.status_error ? (
              <>
                <dt>Ошибка</dt>
                <dd>{doc.status_error}</dd>
              </>
            ) : null}
          </dl>
          <p className="muted">
            Показано строк: {doc.lines_returned} из {doc.lines_total}
          </p>
          <pre className="doc-text">{doc.text}</pre>
          <div className="row">
            {doc.lines_returned < doc.lines_total ? (
              <button type="button" onClick={() => setAll(true)}>
                Показать всё
              </button>
            ) : null}
            {doc.download_url ? (
              <a className="button" href={downloadHref(doc.download_url)} download>
                Скачать оригинал
              </a>
            ) : null}
          </div>
        </>
      ) : null}
    </Modal>
  );
}
