import { vi } from "vitest";

export type Handler = (url: URL, init: RequestInit | undefined) => { status?: number; body: unknown } | undefined;

/** Подменяет fetch: обработчики по очереди; первый вернувший ответ — побеждает. */
export function mockFetch(...handlers: Handler[]) {
  const calls: { url: URL; init: RequestInit | undefined }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    calls.push({ url, init });
    for (const h of handlers) {
      const r = h(url, init);
      if (r) return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({ error: "not_found", detail: url.pathname }), { status: 404 });
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

export const route = (method: string, path: string | RegExp, body: unknown, status = 200): Handler =>
  (url, init) => {
    const m = (init?.method ?? "GET").toUpperCase();
    const ok = typeof path === "string" ? url.pathname === path : path.test(url.pathname);
    return m === method && ok ? { status, body: typeof body === "function" ? (body as (u: URL) => unknown)(url) : body } : undefined;
  };
