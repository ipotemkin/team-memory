#!/usr/bin/env bash
# Этап 8: восстановление бэкапа scripts/backup.sh. РАЗРУШИТЕЛЬНО: заменяет объекты БД и содержимое бакета.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=${1:?usage: restore.sh backups/<timestamp>}
BUCKET=${S3_BUCKET:-documents}
test -f "$DIR/kb.dump" || { echo "no $DIR/kb.dump"; exit 1; }
# Сначала проверяем целостность бэкапа, и только потом что-то трогаем.
( cd "$DIR" && sha256sum -c --quiet SHA256SUMS )
DIR_ABS=$(cd "$DIR" && pwd)

# Останавливаем писателей, чтобы во время восстановления не появлялись новые данные.
echo "==> stopping api/worker/beat"
docker compose stop api worker beat || true
echo "==> pg_restore $DIR/kb.dump"
# --clean --if-exists: удалить и пересоздать объекты; --no-owner: не восстанавливать владельцев.
docker compose exec -T postgres pg_restore -U kb -d kb --clean --if-exists --no-owner < "$DIR/kb.dump"

# --remove: удалить из бакета объекты, которых нет в бэкапе (точная копия).
echo "==> mc mirror $DIR/minio/$BUCKET -> local/$BUCKET"
docker compose run --rm --no-deps -v "$DIR_ABS/minio:/backup:ro" --entrypoint /bin/sh minio-init -c \
  "mc alias set local http://minio:9000 minioadmin minioadmin >/dev/null && mc mb --ignore-existing local/$BUCKET && \
   mc mirror --overwrite --remove /backup/$BUCKET local/$BUCKET"

echo "==> starting api/worker/beat"
docker compose up -d api worker beat
echo "restored from $DIR"
