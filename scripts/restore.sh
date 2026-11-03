#!/usr/bin/env bash
# Stage 8: restore a backup made by scripts/backup.sh. DESTRUCTIVE: replaces DB objects and bucket content.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=${1:?usage: restore.sh backups/<timestamp>}
BUCKET=${S3_BUCKET:-documents}
test -f "$DIR/kb.dump" || { echo "no $DIR/kb.dump"; exit 1; }
( cd "$DIR" && sha256sum -c --quiet SHA256SUMS )
DIR_ABS=$(cd "$DIR" && pwd)

echo "==> stopping api/worker/beat"
docker compose stop api worker beat || true
echo "==> pg_restore $DIR/kb.dump"
docker compose exec -T postgres pg_restore -U kb -d kb --clean --if-exists --no-owner < "$DIR/kb.dump"

echo "==> mc mirror $DIR/minio/$BUCKET -> local/$BUCKET"
docker compose run --rm --no-deps -v "$DIR_ABS/minio:/backup:ro" --entrypoint /bin/sh minio-init -c \
  "mc alias set local http://minio:9000 minioadmin minioadmin >/dev/null && mc mb --ignore-existing local/$BUCKET && \
   mc mirror --overwrite --remove /backup/$BUCKET local/$BUCKET"

echo "==> starting api/worker/beat"
docker compose up -d api worker beat
echo "restored from $DIR"
