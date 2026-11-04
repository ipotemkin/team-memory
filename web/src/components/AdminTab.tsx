// Вкладка «Админ»: вход по токену, состояние сервисов, реестр моделей эмбеддингов и blue/green-переиндексация.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ADMIN_TOKEN_KEY, adminApi, ApiError, errorMessage } from "../api/client";
import type { EmbeddingModel, HealthReport } from "../api/types";
import { useToast } from "./Toasts";

// Подписи статусов модели в реестре.
const STATUS_LABEL: Record<EmbeddingModel["status"], string> = {
  indexing: "индексация",
  ready: "готова",
  active: "активна",
  retired: "выведена",
  failed: "ошибка",
};

// Токен из sessionStorage; если хранилище недоступно (приватный режим и т.п.) — пусто.
function readToken(): string {
  try {
    return sessionStorage.getItem(ADMIN_TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Вкладка администрирования. Данные грузятся только когда вкладка активна (`active`). */
export function AdminTab({ active }: { active: boolean }) {
  const toast = useToast();
  const [token, setToken] = useState<string>(readToken);
  const [draft, setDraft] = useState("");
  const [models, setModels] = useState<EmbeddingModel[] | null>(null);
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [form, setForm] = useState({ provider: "ollama", name: "", dimension: "1024" });
  const [busy, setBusy] = useState<string | null>(null);

  // 401 — токен неверный: забываем его и возвращаемся к форме входа.
  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) {
        sessionStorage.removeItem(ADMIN_TOKEN_KEY);
        setToken("");
      }
      toast(errorMessage(e));
    },
    [toast],
  );

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [m, h] = await Promise.all([adminApi.listModels(token), adminApi.health(token)]);
      setModels(m);
      setHealth(h);
    } catch (e) {
      fail(e);
    }
  }, [token, fail]);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  // прогресс переиндексации: опрашиваем, пока есть модели в статусе indexing
  const indexing = models?.some((m) => m.status === "indexing") ?? false;
  useEffect(() => {
    if (!active || !token || !indexing) return;
    const t = setInterval(() => {
      adminApi.listModels(token).then(setModels).catch(fail);
    }, 2000);
    return () => clearInterval(t);
  }, [active, token, indexing, fail]);

  const login = (e: FormEvent) => {
    e.preventDefault();
    const v = draft.trim();
    if (!v) return;
    sessionStorage.setItem(ADMIN_TOKEN_KEY, v);
    setToken(v);
    setDraft("");
  };
  const logout = () => {
    sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    setToken("");
    setModels(null);
    setHealth(null);
  };

  // Обёртка для действий над моделями: блокирует кнопки, показывает результат и перечитывает данные.
  const act = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key);
    try {
      await fn();
      toast(ok);
      await load();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const register = (e: FormEvent) => {
    e.preventDefault();
    const dimension = Number(form.dimension);
    void act(
      "register",
      () => adminApi.registerModel(token, { provider: form.provider.trim(), name: form.name.trim(), dimension }),
      `Модель ${form.name.trim()} зарегистрирована`,
    ).then(() => setForm((f) => ({ ...f, name: "" })));
  };

  if (!token) {
    return (
      <section className="stack">
        <h2>Администрирование</h2>
        <form className="login-form" onSubmit={login}>
          <label htmlFor="admin-token">Админ-токен</label>
          <div className="login-row">
            <input id="admin-token" type="password" value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="off" />
            <button type="submit">Войти</button>
          </div>
        </form>
        <p className="muted">Токен хранится только в sessionStorage этой вкладки (ADMIN_TOKEN на сервере).</p>
      </section>
    );
  }

  return (
    <section className="stack">
      <div className="row between">
        <h2>Администрирование</h2>
        <div className="row">
          <button type="button" onClick={() => void load()}>Обновить</button>
          <button type="button" onClick={logout}>Выйти</button>
        </div>
      </div>

      <h3>Состояние сервисов</h3>
      {health ? (
        <table aria-label="Состояние сервисов">
          <thead><tr><th>Компонент</th><th>Статус</th><th>Детали</th></tr></thead>
          <tbody>
            {Object.entries(health.components).map(([name, c]) => (
              <tr key={name}>
                <td>{name}</td>
                <td>{c.ok ? "ok" : "ошибка"}</td>
                <td>{c.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p aria-busy="true">Загрузка…</p>}

      <h3>Модели эмбеддингов</h3>
      {models ? (
        <table aria-label="Модели эмбеддингов">
          <thead>
            <tr><th>Модель</th><th>Провайдер</th><th>Размерность</th><th>Статус</th><th>Прогресс</th><th>Действия</th></tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.name}>
                <td>{m.name}</td>
                <td>{m.provider}</td>
                <td>{m.dimension}</td>
                <td>
                  {m.status === "failed" ? (
                    <span className="badge status-failed" title={m.error ?? undefined}>failed</span>
                  ) : STATUS_LABEL[m.status]}
                  {m.error ? <div className="error" role="alert">{m.error}</div> : null}
                </td>
                <td>
                  <progress max={1} value={m.progress} aria-label={`Прогресс ${m.name}`} /> {m.progress_done}/{m.progress_total}
                </td>
                <td className="row">
                  {m.status !== "active" ? (
                    <>
                      <button type="button" disabled={busy !== null}
                        onClick={() => void act(`r:${m.name}`, () => adminApi.reindex(token, m.name), `Переиндексация ${m.name} запущена`)}>
                        Переиндексировать
                      </button>
                      {/* Активировать можно только полностью проиндексированную (ready) модель — сервер проверяет то же. */}
                      <button type="button" disabled={busy !== null || m.status !== "ready"}
                        onClick={() => void act(`a:${m.name}`, () => adminApi.activate(token, m.name), `Модель ${m.name} активна`)}>
                        Активировать
                      </button>
                      <button type="button" disabled={busy !== null}
                        onClick={() => {
                          if (window.confirm(`Удалить данные модели ${m.name}?`))
                            void act(`d:${m.name}`, () => adminApi.deleteModel(token, m.name), `Модель ${m.name} удалена`);
                        }}>
                        Удалить
                      </button>
                    </>
                  ) : <span className="muted">используется для поиска</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p aria-busy="true">Загрузка…</p>}

      <h3>Зарегистрировать модель</h3>
      <form className="row" onSubmit={register} aria-label="Новая модель">
        <label>
          Провайдер
          <select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}>
            <option value="ollama">ollama</option>
            <option value="openai_compat">openai_compat</option>
            <option value="stub">stub</option>
          </select>
        </label>
        <label>
          Имя модели
          <input value={form.name} required onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="bge-m3" />
        </label>
        <label>
          Размерность
          <input type="number" min={1} max={4000} required value={form.dimension}
            onChange={(e) => setForm({ ...form, dimension: e.target.value })} />
        </label>
        <button type="submit" disabled={busy !== null}>Зарегистрировать</button>
      </form>
    </section>
  );
}
