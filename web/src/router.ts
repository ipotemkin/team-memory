import { useEffect, useState } from "react";

export type TabId = "documents" | "ask" | "history" | "admin";

export interface Route {
  tab: TabId;
  documentId: string | null;
  runId: string | null;
}

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [head, id] = parts;
  if (head === "ask") return { tab: "ask", documentId: null, runId: null };
  if (head === "admin") return { tab: "admin", documentId: null, runId: null };
  if (head === "history") return { tab: "history", documentId: null, runId: id ?? null };
  return { tab: "documents", documentId: head === "documents" ? (id ?? null) : null, runId: null };
}

export const hrefs = {
  documents: "#/documents",
  document: (id: string) => `#/documents/${encodeURIComponent(id)}`,
  ask: "#/ask",
  history: "#/history",
  admin: "#/admin",
  run: (id: string) => `#/history/${encodeURIComponent(id)}`,
};

export function navigate(href: string): void {
  window.location.hash = href;
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
