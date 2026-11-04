// Тесты вкладки «Вопросы»: отправка, ответ, источники, ошибки.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { mockFetch, route } from "./mockFetch";

describe("Сценарий 2: вопросы", () => {
  it("Ctrl+Enter задаёт вопрос, показывает ответ, источники и бейдж проверки; ссылка открывает документ", async () => {
    let resolve: (() => void) | null = null;
    const gate = new Promise<void>((r) => (resolve = r));
    const base = mockFetch(
      route("GET", "/kb/documents", []),
      route("GET", "/kb/documents/42", {
        id: "42", title: "Отпуск", created_at: "2026-11-01T10:00:00Z", status: "ready", status_error: null,
        text: "Отпуск 28 дней", lines_total: 1, lines_returned: 1, mime_type: null, size_bytes: null, sha256: null,
        download_url: null, snippets_count: 1,
      }),
    );
    const orig = base.fn.getMockImplementation()!;
    base.fn.mockImplementation(async (input, init) => {
      if (String(input).endsWith("/kb/ask")) {
        base.calls.push({ url: new URL(String(input)), init });
        await gate;
        return new Response(JSON.stringify({ answer: "28 дней", sources: [{ document_id: "42", quote: "Отпуск 28 дней" }], needs_review: true }));
      }
      return orig(input, init);
    });
    window.location.hash = "#/ask";
    const user = userEvent.setup();
    render(<App />);
    const box = screen.getByLabelText("Вопрос");
    await user.type(box, "Сколько дней отпуска?");
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(await screen.findByRole("button", { name: "Ищем ответ…" })).toBeDisabled();
    resolve!();
    const answer = await screen.findByRole("region", { name: "Ответ" });
    expect(within(answer).getByText("28 дней")).toBeInTheDocument();
    expect(within(answer).getByText("Отпуск 28 дней")).toBeInTheDocument();
    expect(within(answer).getByText("требует проверки")).toBeInTheDocument();
    const call = base.calls.find((c) => c.url.pathname === "/kb/ask");
    expect(JSON.parse(String(call?.init?.body))).toEqual({ question: "Сколько дней отпуска?" });

    await user.click(within(answer).getByRole("link", { name: "Документ #42" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Отпуск 28 дней")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Закрыть" }));
    await waitFor(() => expect(window.location.hash).toBe("#/ask"));
    expect(screen.getByText("28 дней")).toBeVisible();
  });

  it("слишком короткий вопрос не отправляется", async () => {
    const { calls } = mockFetch(route("GET", "/kb/documents", []));
    window.location.hash = "#/ask";
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Вопрос"), "аб");
    await user.click(screen.getByRole("button", { name: "Спросить" }));
    expect(screen.getByText(/не короче 3/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.pathname === "/kb/ask")).toBe(false);
  });

  it("ошибка API показывается toast", async () => {
    mockFetch(route("GET", "/kb/documents", []), route("POST", "/kb/ask", { error: "internal_error", detail: null }, 500));
    window.location.hash = "#/ask";
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Вопрос"), "Вопрос?");
    await user.click(screen.getByRole("button", { name: "Спросить" }));
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("Внутренняя ошибка сервера");
  });
});
