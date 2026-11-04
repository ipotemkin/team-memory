// Список источников ответа: дословная цитата и ссылка на документ.
import type { Source } from "../api/types";
import { hrefs } from "../router";

/** Источники ответа; ключ включает индекс — из одного документа может быть несколько цитат. */
export function SourcesList({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return <p className="muted">Источники не найдены.</p>;
  return (
    <ol className="sources">
      {sources.map((s, i) => (
        <li key={`${s.document_id}-${i}`}>
          <blockquote>{s.quote}</blockquote>
          <a href={hrefs.document(s.document_id)}>Документ #{s.document_id}</a>
        </li>
      ))}
    </ol>
  );
}
