#!/bin/sh
set -eu
cd /opt/tochka
docker cp database.dump tochka-postgres-1:/tmp/initial.dump
docker compose -f project/deploy/compose.yml exec -T postgres sh -c '
  existing=$(psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema = '\''public'\''")
  if [ "$existing" != 0 ]; then
    echo "Database already contains tables; refusing initial restore."
    exit 1
  fi
  pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB" /tmp/initial.dump
'
docker compose -f project/deploy/compose.yml up -d --no-build admin miniapp proxy
