import { useCallback, useRef } from "react";
import { AdminTab } from "./components/AdminTab";
import { AskTab } from "./components/AskTab";
import { DocumentView } from "./components/DocumentView";
import { DocumentsTab } from "./components/DocumentsTab";
import { HistoryTab } from "./components/HistoryTab";
import { ToastProvider } from "./components/Toasts";
import { hrefs, navigate, useRoute, type TabId } from "./router";

const TABS: { id: TabId; label: string; href: string }[] = [
  { id: "documents", label: "Документы", href: hrefs.documents },
  { id: "ask", label: "Вопросы", href: hrefs.ask },
  { id: "history", label: "История", href: hrefs.history },
  { id: "admin", label: "Админ", href: hrefs.admin },
];

export default function App() {
  const route = useRoute();
  // Документ открывается поверх вкладки, с которой перешли; закрытие возвращает назад.
  const lastTab = useRef<string>(hrefs.documents);
  if (!route.documentId) lastTab.current = TABS.find((t) => t.id === route.tab)?.href ?? hrefs.documents;
  const active: TabId = route.documentId && lastTab.current !== hrefs.documents
    ? (TABS.find((t) => t.href === lastTab.current)?.id ?? "documents")
    : route.tab;
  const closeDoc = useCallback(() => navigate(lastTab.current), []);

  return (
    <ToastProvider>
      <div className="app">
        <header className="top">
          <h1>Team Memory</h1>
          <nav aria-label="Разделы">
            <ul className="tabs">
              {TABS.map((t) => (
                <li key={t.id}>
                  <a href={t.href} aria-current={active === t.id ? "page" : undefined} className={active === t.id ? "active" : ""}>
                    {t.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </header>
        <main>
          {/* вкладки остаются смонтированными, чтобы не терять ответ/формы при переходах */}
          <div hidden={active !== "documents"}><DocumentsTab /></div>
          <div hidden={active !== "ask"}><AskTab /></div>
          <div hidden={active !== "admin"}><AdminTab active={active === "admin"} /></div>
          <div hidden={active !== "history"}><HistoryTab runId={route.runId} active={active === "history"} /></div>
        </main>
        {route.documentId ? <DocumentView id={route.documentId} onClose={closeDoc} /> : null}
      </div>
    </ToastProvider>
  );
}
