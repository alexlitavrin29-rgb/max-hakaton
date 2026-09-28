#!/bin/sh
set -eu
umask 077
cd /opt/tochka
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose -f project/deploy/compose.yml exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backups/database-$stamp.dump"
tar -czf "backups/config-$stamp.tar.gz" .env .deploy-secrets.env project/deploy project/llm/data/help_points.json
echo "Backup complete: $stamp"
