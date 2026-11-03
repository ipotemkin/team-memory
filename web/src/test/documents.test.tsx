import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import { mockFetch, route } from "./mockFetch";

const doc = (id: string, status: string, title = `Док ${id}`, status_error: string | null = null) => ({
  id, title, created_at: "2026-11-01T10:00:00Z", status, status_error,
});

describe("Сценарий 1: документы", () => {
  it("добавляет документ, показывает индексацию и автообновляет до ready", async () => {
    let created = false;
    let polls = 0;
    const { calls } = mockFetch(
      route("POST", "/kb/documents", () => {
        created = true;
        return { status: "ok", document_id: "42", document_status: "indexing" };
      }),
      route("GET", "/kb/documents", () => {
        if (!created) return [];
        polls += 1;
        return [doc("42", polls > 1 ? "ready" : "indexing", "Отпуск")];
      }),
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<App />);
    expect(await screen.findByText("Документов пока нет.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Заголовок"), "Отпуск");
    await user.type(screen.getByLabelText("Текст"), "Отпуск 28 дней");
    await user.click(screen.getByRole("button", { name: "Добавить" }));

    const post = calls.find((c) => c.init?.method === "POST");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ title: "Отпуск", text: "Отпуск 28 дней" });
    expect(await screen.findByText("индексируется")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(await screen.findByText("готов")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("валидирует на клиенте и показывает 422 по полям", async () => {
    const { calls } = mockFetch(
      route("GET", "/kb/documents", []),
      route("POST", "/kb/documents", { error: "validation_error", detail: [{ field: "body.title", msg: "Заголовок уже занят" }] }, 422),
    );
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Добавить" }));
    expect(screen.getByText("Введите заголовок")).toBeInTheDocument();
    expect(screen.getByText("Введите текст документа")).toBeInTheDocument();
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);

    await user.type(screen.getByLabelText("Заголовок"), "   ");
    expect(screen.getByLabelText("Заголовок")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Заголовок"));
    await user.type(screen.getByLabelText("Заголовок"), "T");
    await user.type(screen.getByLabelText("Текст"), "x");
    await user.click(screen.getByRole("button", { name: "Добавить" }));
    expect(await screen.findByText("Заголовок уже занят")).toBeInTheDocument();
    expect(screen.getByLabelText("Заголовок")).toHaveAttribute("aria-invalid", "true");
  });

  it("загружает файл и отклоняет неподдерживаемый тип", async () => {
    const { calls } = mockFetch(
      route("GET", "/kb/documents", []),
      route("POST", "/kb/documents", { status: "ok", document_id: "7", document_status: "ready" }),
    );
    const user = userEvent.setup({ applyAccept: false });
    render(<App />);
    const input = await screen.findByLabelText(/Файл/);
    await user.upload(input, new File(["MZ"], "a.exe", { type: "application/octet-stream" }));
    await user.click(screen.getByRole("button", { name: "Загрузить" }));
    expect(screen.getByText("Поддерживаются только .txt, .md, .docx и .pdf")).toBeInTheDocument();

    await user.upload(input, new File(["# hi"], "notes.md", { type: "text/markdown" }));
    await user.click(screen.getByRole("button", { name: "Загрузить" }));
    await waitFor(() => expect(calls.some((c) => c.init?.body instanceof FormData)).toBe(true));
    const fd = calls.find((c) => c.init?.body instanceof FormData)?.init?.body as FormData;
    expect((fd.get("file") as File).name).toBe("notes.md");
    expect(await screen.findByText("Документ #7 добавлен")).toBeInTheDocument();
  });

  it("файловый input принимает .txt/.md/.docx/.pdf, показывает подсказку и отправляет PDF", async () => {
    const { calls } = mockFetch(
      route("GET", "/kb/documents", []),
      route("POST", "/kb/documents", { status: "ok", document_id: "8", document_status: "ready" }),
    );
    const user = userEvent.setup();
    render(<App />);
    const input = await screen.findByLabelText(/Файл/);
    expect(input).toHaveAttribute("accept", ".txt,.md,.docx,.pdf");
    expect(screen.getByText(/из PDF — текстовый слой/)).toBeInTheDocument();
    expect(input.getAttribute("aria-describedby")).toContain("doc-file-hint");
    await user.upload(input, new File(["%PDF-1.7"], "Регламент.PDF", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Загрузить" }));
    await waitFor(() => expect(calls.some((c) => c.init?.body instanceof FormData)).toBe(true));
    const fd = calls.find((c) => c.init?.body instanceof FormData)?.init?.body as FormData;
    expect((fd.get("file") as File).name).toBe("Регламент.PDF");
    expect(await screen.findByText("Документ #8 добавлен")).toBeInTheDocument();
  });

  it("deep link #/documents/{id} открывает документ: первые строки, показать всё, скачать", async () => {
    mockFetch(
      route("GET", "/kb/documents", [doc("5", "failed", "Док 5", "embedding failed")]),
      route("GET", "/kb/documents/5", (u: URL) => ({
        ...doc("5", "failed"),
        status_error: "embedding failed",
        text: u.searchParams.get("lines") ? "строка 1" : "строка 1\nстрока 2",
        lines_total: 2,
        lines_returned: u.searchParams.get("lines") ? 1 : 2,
        mime_type: "text/plain",
        size_bytes: 10,
        sha256: "x",
        download_url: "http://minio/file.txt",
        snippets_count: 0,
      })),
    );
    window.location.hash = "#/documents/5";
    const user = userEvent.setup();
    render(<App />);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Показано строк: 1 из 2")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Скачать оригинал" })).toHaveAttribute("href", "http://minio/file.txt");
    await user.click(within(dialog).getByRole("button", { name: "Показать всё" }));
    expect(await within(dialog).findByText("Показано строк: 2 из 2")).toBeInTheDocument();
    // tooltip со status_error на бейдже в таблице
    await waitFor(() => expect(screen.getAllByTestId("status-badge")[0]).toHaveAttribute("title", "embedding failed"));
  });

  it("подсказка status_error берётся из списка без доп. запросов", async () => {
    const { calls } = mockFetch(route("GET", "/kb/documents", [doc("8", "failed", "Сломан", "embedding failed"), doc("9", "ready")]));
    render(<App />);
    const badge = await screen.findByText("ошибка");
    expect(badge.closest("[data-testid=status-badge]")).toHaveAttribute("title", "embedding failed");
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.every((c) => c.url.pathname === "/kb/documents")).toBe(true);
  });

  it("сетевая ошибка показывает toast", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    render(<App />);
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("Сервер недоступен");
  });
});
