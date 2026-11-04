// Тесты вкладки «Админ»: вход по токену, реестр моделей, переиндексация, активация.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { ADMIN_TOKEN_KEY } from "../api/client";
import { mockFetch, route } from "./mockFetch";

const model = (name: string, status: string, dimension = 64, progress = 1) => ({
  name, provider: "stub", dimension, status, progress_done: progress * 10, progress_total: 10, progress,
  created_at: "2026-11-04T00:00:00Z", activated_at: null, retired_at: null, error: null as string | null,
});
const health = { status: "ok", components: { postgres: { ok: true, detail: "ok" }, embedder: { ok: true, detail: "stub dim=64" } } };

describe("Админ", () => {
  beforeEach(() => sessionStorage.clear());

  it("вход по токену, список моделей, регистрация, переиндексация и активация", async () => {
    let models = [model("stub:hash-64", "active")];
    const { calls } = mockFetch(
      route("GET", "/kb/documents", []),
      route("GET", "/admin/health", health),
      route("GET", "/admin/embedding-models", () => models),
      (url, init) => {
        if (init?.method === "POST" && url.pathname === "/admin/embedding-models") {
          const b = JSON.parse(String(init.body)) as { name: string; dimension: number };
          models = [...models, model(b.name, "indexing", b.dimension, 0)];
          return { status: 201, body: models.at(-1) };
        }
        if (init?.method === "POST" && url.pathname.endsWith("/reindex")) {
          models = models.map((m) => (m.name === "bge-m3" ? model("bge-m3", "ready", 1024) : m));
          return { status: 202, body: { task_id: "t1", model: models.at(-1) } };
        }
        if (init?.method === "POST" && url.pathname.endsWith("/activate")) {
          models = [model("stub:hash-64", "retired"), model("bge-m3", "active", 1024)];
          return { body: models[1] };
        }
        return undefined;
      },
    );
    window.location.hash = "#/admin";
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Админ-токен"), "s3cret");
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(sessionStorage.getItem(ADMIN_TOKEN_KEY)).toBe("s3cret");

    const table = await screen.findByRole("table", { name: "Модели эмбеддингов" });
    expect(within(table).getByText("stub:hash-64")).toBeInTheDocument();
    expect(within(table).getByText("активна")).toBeInTheDocument();
    expect(await screen.findByText("stub dim=64")).toBeInTheDocument();
    const adminCall = calls.find((c) => c.url.pathname === "/admin/embedding-models")!;
    expect(new Headers(adminCall.init?.headers).get("X-Admin-Token")).toBe("s3cret");

    await user.type(screen.getByLabelText("Имя модели"), "bge-m3");
    await user.click(screen.getByRole("button", { name: "Зарегистрировать" }));
    const row = (await within(table).findByText("bge-m3")).closest("tr")!;
    expect(within(row).getByText("индексация")).toBeInTheDocument();
    expect(within(row).getByText("1024")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Активировать" })).toBeDisabled();

    await user.click(within(row).getByRole("button", { name: "Переиндексировать" }));
    await waitFor(() => expect(within(row).getByText("готова")).toBeInTheDocument());
    await user.click(within(row).getByRole("button", { name: "Активировать" }));
    await waitFor(() => expect(within(row).getByText("активна")).toBeInTheDocument());
    expect(calls.some((c) => c.url.pathname === "/admin/embedding-models/bge-m3/activate")).toBe(true);
  });

  it("неверный токен сбрасывается", async () => {
    sessionStorage.setItem(ADMIN_TOKEN_KEY, "bad");
    mockFetch(
      route("GET", "/kb/documents", []),
      route("GET", "/admin/health", { error: "unauthorized", detail: "invalid" }, 401),
      route("GET", "/admin/embedding-models", { error: "unauthorized", detail: "invalid" }, 401),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.location.hash = "#/admin";
    render(<App />);
    expect(await screen.findByLabelText("Админ-токен")).toBeInTheDocument();
    expect(sessionStorage.getItem(ADMIN_TOKEN_KEY)).toBeNull();
  });

  it("показывает ошибку переиндексации и бейдж failed", async () => {
    sessionStorage.setItem(ADMIN_TOKEN_KEY, "s3cret");
    const failed = { ...model("bge-m3", "failed", 1024, 0.3), error: "RuntimeError: embedding server down" };
    mockFetch(
      route("GET", "/kb/documents", []),
      route("GET", "/admin/health", health),
      route("GET", "/admin/embedding-models", [model("stub:hash-64", "active"), failed]),
    );
    window.location.hash = "#/admin";
    render(<App />);
    const table = await screen.findByRole("table", { name: "Модели эмбеддингов" });
    const row = (await within(table).findByText("bge-m3")).closest("tr")!;
    const badge = within(row).getByText("failed");
    expect(badge).toHaveClass("badge", "status-failed");
    const err = within(row).getByRole("alert");
    expect(err).toHaveTextContent("RuntimeError: embedding server down");
    expect(err).toHaveClass("error");
    expect(within(row).getByRole("button", { name: "Переиндексировать" })).toBeEnabled();
    expect(within(row).getByRole("button", { name: "Активировать" })).toBeDisabled();
  });
});
