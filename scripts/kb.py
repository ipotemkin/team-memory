#!/usr/bin/env python3
"""Инструменты датасета tests_data/ против запущенного API (только стандартная библиотека).

    python3 scripts/kb.py seed             # загрузить tests_data/kb_documents.jsonl, идемпотентно по
    title
    python3 scripts/kb.py eval             # задать вопросы kb_questions.jsonl и сверить; exit 1 при
    расхождении
    python3 scripts/kb.py acceptance --yes # сброс данных → seed → eval → аудит → итог по критериям

Окружение: API_URL (по умолчанию http://localhost:8000),
     PSQL (по умолчанию "docker compose exec -T postgres psql -U kb -d kb"; напр. "psql
     postgresql://kb:kb@127.0.0.1:5432/kb").
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
PSQL = os.environ.get("PSQL", "docker compose exec -T postgres psql -U kb -d kb")
WAIT_S = int(os.environ.get("SEED_WAIT_S", "180"))
# Только таблицы данных схемы app; реестр app.embedding_models и alembic_version сохраняются.
_DATA_TABLES = "app.snippet_embeddings, app.snippets, app.qa_runs, app.documents, app.audit_runs"
RESET_SQL = f"TRUNCATE {_DATA_TABLES} RESTART IDENTITY CASCADE"


def call(method: str, path: str, body: Any = None) -> Any:
    """HTTP-запрос к API с JSON-телом; ответ — разобранный JSON.

    Raises:
        SystemExit: HTTP-ошибка или API недоступен (скрипт завершается с понятным сообщением).
    """
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read()[:300]!r}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"API недоступен ({API}): {e.reason}") from e


def jsonl(name: str) -> list[dict[str, Any]]:
    """Строки файла tests_data/<name> в формате JSON Lines."""
    return [json.loads(x) for x in (ROOT / "tests_data" / name).read_text("utf-8").splitlines() if x.strip()]


def psql(sql: str) -> list[list[str]]:
    """Выполняет SQL через команду PSQL; строки результата — списки полей (разделитель TAB)."""
    cmd = [*shlex.split(PSQL), "-tA", "-F", "\t", "-v", "ON_ERROR_STOP=1", "-c", sql]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return [line.split("\t") for line in out.splitlines() if line]


def all_documents() -> list[dict[str, Any]]:
    """Все документы из API (постранично по 200)."""
    out: list[dict[str, Any]] = []
    while page := call("GET", f"/kb/documents?limit=200&offset={len(out)}"):
        out += page
        if len(page) < 200:
            break
    return out


# --- seed --------------------------------------------------------------------------------------


def seed() -> dict[str, str]:
    """Добавляет недостающие документы датасета и ждёт, пока все станут ready (не дольше SEED_WAIT_S).

    Returns:
        Словарь title → document_id.
    """
    have = {d["title"]: d["id"] for d in all_documents()}
    added = 0
    for d in jsonl("kb_documents.jsonl"):
        if d["title"] not in have:
            have[d["title"]] = call("POST", "/kb/documents", {"title": d["title"], "text": d["text"]})[
                "document_id"
            ]
            added += 1
    wanted = {d["title"]: have[d["title"]] for d in jsonl("kb_documents.jsonl")}
    deadline = time.time() + WAIT_S
    for title, i in wanted.items():
        while (st := call("GET", f"/kb/documents/{i}?lines=1"))["status"] == "indexing":
            if time.time() > deadline:
                raise SystemExit(f"документ {i} всё ещё indexing через {WAIT_S}s (worker запущен?)")
            time.sleep(1)
        if st["status"] != "ready":
            raise SystemExit(f"документ {i} «{title}»: status={st['status']} ({st.get('status_error')})")
    print(f"seed: добавлено {added}, уже было {len(wanted) - added}, все {len(wanted)} в статусе ready")
    return wanted


# --- eval --------------------------------------------------------------------------------------


@dataclass
class Row:
    """Результат одного вопроса eval: ответ, источники, причина ревью, время и найденные расхождения."""

    n: int
    q: dict[str, Any]
    needs_review: bool
    sources: int
    doc_ok: bool | None
    reason: str | None
    ms: int
    problems: list[str] = field(default_factory=list)


def evaluate() -> list[Row]:
    """Задаёт все вопросы датасета и сверяет needs_review, источники и причину с ожиданиями.

    Причина берётся из последней записи qa_runs (запросы идут последовательно).
    """
    titles = {d["id"]: d["title"] for d in all_documents()}
    rows = []
    for n, q in enumerate(jsonl("kb_questions.jsonl"), 1):
        t0 = time.time()
        r = call("POST", "/kb/ask", {"question": q["question"]})
        ms = int((time.time() - t0) * 1000)
        run = call("GET", "/kb/qa_runs?limit=1")[0]
        src_docs = [titles.get(s["document_id"]) for s in r["sources"]]
        row = Row(n, q, r["needs_review"], len(r["sources"]), None, run["error"], ms)
        if r["needs_review"] != q["expected_needs_review"]:
            row.problems.append(f"needs_review={r['needs_review']}, ожидали {q['expected_needs_review']}")
        if q["expected_needs_review"]:
            if r["sources"]:
                row.problems.append("у ответа «вне базы» есть источники")
            if not run["error"]:
                row.problems.append("нет причины проверки в qa_runs")
        else:
            row.doc_ok = q.get("expected_document") in src_docs
            if not r["sources"]:
                row.problems.append("пустые sources")
            elif not row.doc_ok:
                row.problems.append(f"источник {src_docs}, ожидали «{q.get('expected_document')}»")
        rows.append(row)
    return rows


def print_eval(rows: list[Row]) -> int:
    """Печатает таблицу результатов; возвращает число вопросов с расхождениями."""

    def yn(b: bool | None) -> str:
        return "-" if b is None else ("да" if b else "НЕТ")

    hdr = ("№", "ожид.", "needs_review", "sources", "док.", "причина", "мс", "итог", "вопрос")
    table = [
        (
            r.n,
            r.q["expected_needs_review"],
            r.needs_review,
            r.sources,
            yn(r.doc_ok),
            r.reason or "-",
            r.ms,
            "OK" if not r.problems else "MISMATCH: " + "; ".join(r.problems),
            r.q["question"][:60],
        )
        for r in rows
    ]
    w = [max(len(str(x)) for x in col) for col in zip(hdr, *table, strict=True)]
    for line in (hdr, *table):
        print("  ".join(str(x).ljust(n) for x, n in zip(line, w, strict=True)))
    bad = sum(bool(r.problems) for r in rows)
    print(f"\n{len(rows) - bad}/{len(rows)} совпадают с ожиданиями")
    if bad:
        print("Подсказка: лишние needs_review -> понизить MIN_SCORE; ответы на вопросы вне базы -> повысить.")
    return bad


# --- acceptance --------------------------------------------------------------------------------


def acceptance(confirm: bool) -> int:
    """Полный приёмочный прогон: очистка данных, seed, eval, /ai, проверка аудита и 4 критериев.

    Args:
        confirm: подтверждение (--yes) — прогон удаляет все данные схемы app.

    Returns:
        Код выхода: 0 — все критерии пройдены, 1 — нет, 2 — нет подтверждения.
    """
    if not confirm:
        print(
            "acceptance удалит ВСЕ документы, qa_runs и audit_runs в схеме app.\n"
            "Запустите с --yes (make acceptance CONFIRM=yes).",
            file=sys.stderr,
        )
        return 2
    call("GET", "/health/live")
    psql(RESET_SQL)
    print("reset: данные схемы app очищены (embedding_models сохранены)")
    docs = jsonl("kb_documents.jsonl")
    qs = jsonl("kb_questions.jsonl")
    wanted = seed()
    listed = {d["title"] for d in all_documents()}
    rows = evaluate()
    bad = print_eval(rows)
    ai = call("POST", "/ai/answer_with_sources", {"question": qs[0]["question"], "context": docs[0]["text"]})

    counts = dict((a, int(c)) for a, c in psql("SELECT action, count(*) FROM app.audit_runs GROUP BY action"))
    neg_rows = psql(
        "SELECT input->>'question', status, coalesce(error,'') FROM app.audit_runs "
        "WHERE action='POST /kb/ask' AND status <> 'ok'"
    )
    neg_audit = {q: (st, err) for q, st, err in neg_rows}
    exp_counts = {"POST /kb/documents": len(docs), "POST /kb/ask": len(qs), "POST /ai/answer_with_sources": 1}
    audit_problems = [
        f"{a}: {counts.get(a, 0)} строк, ожидали {n}" for a, n in exp_counts.items() if counts.get(a) != n
    ]
    for q in (q for q in qs if q["expected_needs_review"]):
        st, err = neg_audit.get(q["question"], ("-", ""))
        if st != "needs_review" or not err:
            audit_problems.append(f"нет причины в audit_runs для «{q['question']}» (status={st})")

    pos = [r for r in rows if not r.q["expected_needs_review"]]
    neg = [r for r in rows if r.q["expected_needs_review"]]
    reasons = ", ".join(r.reason or "-" for r in neg)
    crit = [
        (
            "1. Документы добавляются и видны в списке",
            set(wanted) <= listed,
            f"{len(set(wanted) & listed)}/{len(docs)} ready в списке",
        ),
        (
            "2. Ответ с источниками из нужного документа",
            all(not r.problems for r in pos),
            f"{sum(not r.problems for r in pos)}/{len(pos)}",
        ),
        (
            "3. Вне базы -> needs_review + причина",
            all(not r.problems for r in neg),
            f"{sum(not r.problems for r in neg)}/{len(neg)}; причины: {reasons}",
        ),
        (
            "4. Каждая команда в audit_runs (+ причины)",
            not audit_problems,
            "; ".join(audit_problems) or ", ".join(f"{a}={counts.get(a, 0)}" for a in exp_counts),
        ),
    ]
    print(f"\n/ai/answer_with_sources: confidence={ai['confidence']}, sources={len(ai['sources'])}")
    print("\n=== Критерии приёмки ===")
    for name, ok, info in crit:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {info}")
    return 0 if all(ok for _, ok, _ in crit) and not bad else 1


def main() -> int:
    """Разбор аргументов CLI и запуск команды."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    sub.add_parser("eval").add_argument(
        "--no-seed", action="store_true", help="не загружать tests_data перед eval"
    )
    sub.add_parser("acceptance").add_argument(
        "--yes", action="store_true", help="подтвердить очистку данных app"
    )
    a = ap.parse_args()
    if a.cmd == "seed":
        seed()
        return 0
    if a.cmd == "eval":
        if not a.no_seed:
            seed()
        return 1 if print_eval(evaluate()) else 0
    return acceptance(a.yes)


if __name__ == "__main__":
    sys.exit(main())
