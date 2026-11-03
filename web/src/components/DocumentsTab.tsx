import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, api, errorMessage } from "../api/client";
import { LIMITS, type DocumentListItem } from "../api/types";
import { formatDate } from "../format";
import { hrefs } from "../router";
import { StatusBadge } from "./Badges";
import { useToast } from "./Toasts";

export const POLL_MS = 2000;

type Errors = Partial<Record<"title" | "text" | "file", string>>;

export function validateDocument(title: string, text: string): Errors {
  const e: Errors = {};
  if (!title.trim()) e.title = "Введите заголовок";
  else if (title.length > LIMITS.titleMax) e.title = `Не более ${LIMITS.titleMax} символов`;
  if (!text.length || !text.trim()) e.text = "Введите текст документа";
  else if (text.length > LIMITS.textMax) e.text = `Не более ${LIMITS.textMax} символов`;
  return e;
}

export function validateFile(file: File | null): string | undefined {
  if (!file) return "Выберите файл";
  const name = file.name.toLowerCase();
  if (!LIMITS.uploadExtensions.some((ext) => name.endsWith(ext))) return "Поддерживаются только .txt и .md";
  if (file.size > LIMITS.uploadMaxBytes) return "Файл больше 20 МБ";
  if (file.size === 0) return "Файл пустой";
  return undefined;
}

export function DocumentsTab() {
  const toast = useToast();
  const [docs, setDocs] = useState<DocumentListItem[] | null>(null);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileTitle, setFileTitle] = useState("");
  const [fileError, setFileError] = useState<string | undefined>();
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setDocs(await api.listDocuments());
    } catch (e) {
      toast(errorMessage(e));
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const indexing = docs?.some((d) => d.status === "indexing") ?? false;
  useEffect(() => {
    if (!indexing) return;
    const t = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(t);
  }, [indexing, load]);

  const created = (id: string) => {
    toast(`Документ #${id} добавлен`, "info");
    void load();
  };

  const submit = async (ev: FormEvent) => {
    ev.preventDefault();
    const v = validateDocument(title, text);
    setErrors(v);
    if (Object.keys(v).length) return;
    setBusy(true);
    try {
      const r = await api.createDocument(title, text);
      setTitle("");
      setText("");
      created(r.document_id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const fe = e.fieldErrors();
        setErrors({ title: fe.title, text: fe.text });
        if (!fe.title && !fe.text) toast(e.message);
      } else toast(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (ev: FormEvent) => {
    ev.preventDefault();
    const err = validateFile(file) ?? (fileTitle.length > LIMITS.titleMax ? `Заголовок не более ${LIMITS.titleMax} символов` : undefined);
    setFileError(err);
    if (err || !file) return;
    setBusy(true);
    try {
      const r = await api.uploadDocument(file, fileTitle.trim() || undefined);
      setFile(null);
      setFileTitle("");
      if (fileInput.current) fileInput.current.value = "";
      created(r.document_id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const fe = e.fieldErrors();
        setFileError(Object.values(fe).join("; ") || e.message);
      } else if (e instanceof ApiError && (e.status === 413 || e.status === 415)) setFileError(e.message);
      else toast(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <div className="grid2">
        <form className="card" onSubmit={submit} noValidate aria-label="Добавить документ">
          <h2>Добавить документ</h2>
          <label htmlFor="doc-title">Заголовок</label>
          <input
            id="doc-title"
            value={title}
            maxLength={LIMITS.titleMax + 50}
            onChange={(e) => setTitle(e.target.value)}
            aria-invalid={!!errors.title}
            aria-describedby={errors.title ? "doc-title-err" : undefined}
          />
          {errors.title ? <p id="doc-title-err" className="field-error">{errors.title}</p> : null}
          <label htmlFor="doc-text">Текст</label>
          <textarea
            id="doc-text"
            rows={6}
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-invalid={!!errors.text}
            aria-describedby={errors.text ? "doc-text-err" : undefined}
          />
          <p className="muted small">{text.length} / {LIMITS.textMax}</p>
          {errors.text ? <p id="doc-text-err" className="field-error">{errors.text}</p> : null}
          <button type="submit" disabled={busy}>Добавить</button>
        </form>
        <form className="card" onSubmit={upload} noValidate aria-label="Загрузить файл">
          <h2>Загрузить файл</h2>
          <label htmlFor="doc-file">Файл (.txt, .md, до 20 МБ)</label>
          <input
            id="doc-file"
            type="file"
            accept=".txt,.md,text/plain,text/markdown"
            ref={fileInput}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            aria-invalid={!!fileError}
            aria-describedby={fileError ? "doc-file-err" : undefined}
          />
          <label htmlFor="doc-file-title">Заголовок (необязательно)</label>
          <input id="doc-file-title" value={fileTitle} onChange={(e) => setFileTitle(e.target.value)} />
          {fileError ? <p id="doc-file-err" className="field-error">{fileError}</p> : null}
          <button type="submit" disabled={busy}>Загрузить</button>
        </form>
      </div>
      <section className="card">
        <div className="row between">
          <h2>Документы</h2>
          <div className="row">
            {indexing ? <span className="muted small" aria-live="polite">Идёт индексация, автообновление…</span> : null}
            <button type="button" className="ghost" onClick={() => void load()}>Обновить</button>
          </div>
        </div>
        {docs === null ? (
          <p aria-busy="true">Загрузка…</p>
        ) : docs.length === 0 ? (
          <p className="muted">Документов пока нет.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">Заголовок</th>
                <th scope="col">Создан</th>
                <th scope="col">ID</th>
                <th scope="col">Статус</th>
                <th scope="col"><span className="sr-only">Действия</span></th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>{d.title}</td>
                  <td>{formatDate(d.created_at)}</td>
                  <td><code>{d.id}</code></td>
                  <td><StatusBadge status={d.status} error={d.status_error ?? (d.status === "failed" ? "Ошибка индексации" : null)} /></td>
                  <td><a href={hrefs.document(d.id)}>Открыть документ</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
