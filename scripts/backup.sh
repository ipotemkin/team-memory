#!/usr/bin/env bash
# Stage 8: Postgres dump (pg_dump -Fc) + MinIO bucket mirror into backups/<timestamp>/.
set -euo pipefail
cd "$(dirname "$0")/.."
TS=$(date +%Y%m%d-%H%M%S)
DIR=${BACKUP_DIR:-backups/$TS}
BUCKET=${S3_BUCKET:-documents}
mkdir -p "$DIR/minio"
DIR_ABS=$(cd "$DIR" && pwd)

echo "==> pg_dump -> $DIR/kb.dump"
docker compose exec -T postgres pg_dump -U kb -d kb -Fc > "$DIR/kb.dump"

echo "==> mc mirror local/$BUCKET -> $DIR/minio/$BUCKET"
docker compose run --rm --no-deps -v "$DIR_ABS/minio:/backup" --entrypoint /bin/sh minio-init -c \
  "mc alias set local http://minio:9000 minioadmin minioadmin >/dev/null && mc mirror --overwrite local/$BUCKET /backup/$BUCKET"

( cd "$DIR" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS )
echo "backup ready: $DIR ($(du -sh "$DIR" | cut -f1))"
