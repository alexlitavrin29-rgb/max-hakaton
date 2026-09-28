#!/bin/sh
# VPS backup for newly created copies. Existing backup files stay untouched.
set -eu
umask 077
cd /opt/tochka
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="backups/managed/$stamp"
mkdir -p "$target"
docker compose -f project/deploy/compose.yml exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$target/database.dump"
tar -czf "$target/configuration.tar.gz" .env .deploy-secrets.env project/deploy project/llm/data/help_points.json
(cd "$target" && sha256sum database.dump configuration.tar.gz > SHA256SUMS && sha256sum -c SHA256SUMS >/dev/null)
docker compose -f project/deploy/compose.yml exec -T postgres pg_restore -l < "$target/database.dump" >/dev/null
touch "$target/.managed-backup"
echo "Managed backup complete: $stamp"
