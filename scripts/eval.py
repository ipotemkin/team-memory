#!/usr/bin/env python3
"""Run tests_data/kb_questions.jsonl against a running API and compare needs_review with expectations.

    python3 scripts/eval.py                 # API_URL=http://localhost:8000, seeds tests_data docs once
    python3 scripts/eval.py --no-seed       # use the documents already in the KB
Use it to retune MIN_SCORE after switching to real embeddings (EMBEDDINGS_PROVIDER=openai_compat).
Exit code 1 if any question deviates from the expectation.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def call(method: str, url: str, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {url} -> {e.code}: {e.read()[:300]!r}") from e


def jsonl(name: str) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (ROOT / "tests_data" / name).read_text("utf-8").splitlines() if x.strip()]


def seed(api: str) -> None:
    have = {d["title"] for d in call("GET", f"{api}/kb/documents?limit=200")}
    ids = []
    for d in jsonl("kb_documents.jsonl"):
        if d["title"] not in have:
            ids.append(call("POST", f"{api}/kb/documents", {"title": d["title"], "text": d["text"]})["document_id"])
    deadline = time.time() + 120
    for i in ids:  # wait for background indexing
        while (st := call("GET", f"{api}/kb/documents/{i}?lines=1")["status"]) == "indexing":
            if time.time() > deadline:
                raise SystemExit(f"document {i} still indexing after 120s (is the worker running?)")
            time.sleep(1)
        if st != "ready":
            raise SystemExit(f"document {i} status={st}")
    print(f"seeded {len(ids)} document(s)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8000"))
    ap.add_argument("--no-seed", action="store_true")
    a = ap.parse_args()
    api = a.api.rstrip("/")
    if not a.no_seed:
        seed(api)
    titles = {d["id"]: d["title"] for d in call("GET", f"{api}/kb/documents?limit=200")}
    rows, bad = [], 0
    for q in jsonl("kb_questions.jsonl"):
        t0 = time.time()
        r = call("POST", f"{api}/kb/ask", {"question": q["question"]})
        ms = int((time.time() - t0) * 1000)
        run = call("GET", f"{api}/kb/qa_runs?limit=1")[0]
        expected_review = not q["answerable"]
        doc_ok = True
        if q["answerable"] and not r["needs_review"]:
            doc_ok = bool(r["sources"]) and titles.get(r["sources"][0]["document_id"]) == q.get("expected_doc")
        ok = r["needs_review"] == expected_review and doc_ok
        if q.get("expected_error") and r["needs_review"]:
            ok = ok and run["error"] == q["expected_error"]
        bad += not ok
        rows.append((q["id"], expected_review, r["needs_review"], run["error"] or "-", run["retrieval_mode"],
                     len(r["sources"]), ms, "OK" if ok else "MISMATCH", q["question"][:50]))
    hdr = ("id", "exp_review", "needs_review", "reason", "mode", "src", "ms", "result", "question")
    w = [max(len(str(x)) for x in col) for col in zip(hdr, *rows, strict=True)]
    for line in (hdr, *rows):
        print("  ".join(str(x).ljust(n) for x, n in zip(line, w, strict=True)))
    print(f"\n{len(rows) - bad}/{len(rows)} match expectations")
    if bad:
        print("Hint: too many false needs_review -> lower MIN_SCORE; answers to out-of-scope questions -> raise it.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
