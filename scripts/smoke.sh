#!/usr/bin/env bash
# Smoke test of the 3 spec scenarios against a running API (default: docker compose stack).
#   API_URL=http://localhost:8000 ./scripts/smoke.sh
#   PSQL="psql postgresql://kb:kb@127.0.0.1:5432/kb" ./scripts/smoke.sh   # without docker
set -euo pipefail
API_URL="${API_URL:-http://localhost:8000}"
PSQL="${PSQL:-docker compose exec -T postgres psql -U kb -d kb}"
TAG="smoke-$(date +%s)"
pass() { printf '\033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*"; exit 1; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'d': d}))" "$1"; }

curl -fsS "$API_URL/health/live" >/dev/null || fail "API is not reachable at $API_URL"

echo "== 1. Добавить документ и увидеть его в списке"
TEXT="Ежедневный стендап проходит в 11:00 и длится не более 15 минут. ($TAG)"
R=$(curl -fsS -X POST "$API_URL/kb/documents" -H 'content-type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"title": sys.argv[1], "text": sys.argv[2]}))' "Правила $TAG" "$TEXT")")
echo "$R"
DOC_ID=$(echo "$R" | json 'd["document_id"]')
[ "$(echo "$R" | json 'd["status"]')" = ok ] || fail "status != ok"
[ "$(echo "$R" | json 'isinstance(d["document_id"], str)')" = True ] || fail "document_id должен быть строкой"
for _ in $(seq 1 60); do  # фоновая индексация (Celery): ждём status=ready
  ST=$(curl -fsS "$API_URL/kb/documents/$DOC_ID?lines=1" | json 'd["status"]')
  [ "$ST" = indexing ] || break; sleep 1
done
[ "$ST" = ready ] && pass "document $DOC_ID проиндексирован (status=ready)" || fail "status=$ST (worker запущен?)"
curl -fsS "$API_URL/kb/documents" | json "any(x['id']=='$DOC_ID' for x in d)" | grep -q True \
  && pass "document $DOC_ID в списке" || fail "document $DOC_ID нет в списке"

echo "== 2. Вопрос с ответом в базе -> источники"
R=$(curl -fsS -X POST "$API_URL/kb/ask" -H 'content-type: application/json' \
  -d '{"question":"Во сколько проходит стендап?"}')
echo "$R"
[ "$(echo "$R" | json 'd["needs_review"]')" = False ] || fail "needs_review должен быть false"
[ "$(echo "$R" | json 'len(d["sources"]) > 0')" = True ] && pass "есть sources" || fail "нет sources"

echo "== 3. Вопрос без ответа -> needs_review + причина в qa_runs + запись аудита"
R=$(curl -fsS -X POST "$API_URL/kb/ask" -H 'content-type: application/json' \
  -d "{\"question\":\"Какая зарплата у тимлида? $TAG\"}")
echo "$R"
[ "$(echo "$R" | json 'd["needs_review"]')" = True ] || fail "needs_review должен быть true"
RUN=$(curl -fsS "$API_URL/kb/qa_runs?needs_review=true&limit=1")
echo "$RUN"
RUN_ID=$(echo "$RUN" | json 'd[0]["id"]')
REASON=$(curl -fsS "$API_URL/kb/qa_runs/$RUN_ID" | json 'd["error"]')
[ -n "$REASON" ] && [ "$REASON" != None ] && pass "qa_run $RUN_ID error=$REASON" || fail "нет причины"
AUDIT=$($PSQL -tA -c "SELECT status FROM app.audit_runs WHERE action='POST /kb/ask' \
  AND input->>'question' LIKE '%$TAG%' ORDER BY id DESC LIMIT 1")
[ "$AUDIT" = needs_review ] && pass "audit_runs: POST /kb/ask status=$AUDIT" || fail "аудит не найден ($AUDIT)"

echo "== bonus: ошибка валидации тоже в аудите"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API_URL/kb/documents" \
  -H 'content-type: application/json' -d '{"title":"","text":""}')
[ "$CODE" = 422 ] && pass "пустой документ -> 422" || fail "ожидали 422, получили $CODE"

echo "ALL SMOKE CHECKS PASSED"
