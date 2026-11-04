// Тесты вкладки «История»: список, фильтр, карточка запроса.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { mockFetch, route } from "./mockFetch";

const run = (id: string, needs_review: boolean, error: string | null = null) => ({
  id, created_at: "2026-11-02T09:00:00Z", question: `Вопрос ${id}`, answer: `Ответ ${id}`,
  sources: needs_review ? [] : [{ document_id: "3", quote: "цитата" }], needs_review, error,
  confidence: needs_review ? 0.2 : 0.9, chat_model: "gpt-4o-mini", embedding_model: "text-embedding-3-small", retrieval_mode: "hybrid",
});

describe("Сценарий 3: история", () => {
  it("фильтр, лимит, обновление и карточка с причиной проверки", async () => {
    const { calls } = mockFetch(
      route("GET", "/kb/documents", []),
      route("GET", "/kb/qa_runs", (u: URL) =>
        u.searchParams.get("needs_review") === "true" ? [run("2", true, "low_confidence")] : [run("1", false), run("2", true, "low_confidence")],
      ),
    );
    window.location.hash = "#/history";
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText("Вопрос 1")).toBeInTheDocument();
    expect(calls.at(-1)!.url.searchParams.get("limit")).toBe("50");

    await user.click(screen.getByLabelText(/только «требует проверки»/));
    await waitFor(() => expect(screen.queryByText("Вопрос 1")).not.toBeInTheDocument());
    expect(calls.at(-1)!.url.searchParams.get("needs_review")).toBe("true");

    await user.selectOptions(screen.getByLabelText("Количество записей"), "100");
    await waitFor(() => expect(calls.at(-1)!.url.searchParams.get("limit")).toBe("100"));
    const n = calls.length;
    await user.click(screen.getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(calls.length).toBe(n + 1));

    await user.click(screen.getByText("Вопрос 2"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Низкая уверенность модели в ответе")).toBeInTheDocument();
    expect(within(dialog).getByText("20%")).toBeInTheDocument();
    expect(within(dialog).getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(within(dialog).getByText("hybrid")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/history/2");
  });

  it("deep link на запуск загружает его и показывает ссылки на документы", async () => {
    mockFetch(route("GET", "/kb/documents", []), route("GET", "/kb/qa_runs", []), route("GET", "/kb/qa_runs/9", run("9", false)));
    window.location.hash = "#/history/9";
    render(<App />);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Ответ 9")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Документ #3" })).toHaveAttribute("href", "#/documents/3");
  });
});
