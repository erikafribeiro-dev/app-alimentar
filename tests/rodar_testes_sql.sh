#!/usr/bin/env bash
# Testa o setup.sql num PostgreSQL local, emulando o Supabase (schemas auth/storage, auth.uid(), papéis).
# Uso:  ./rodar_testes_sql.sh            (precisa de psql apontando para um Postgres onde você possa criar bancos)
# Ex.:  PGHOST=localhost PGUSER=postgres ./rodar_testes_sql.sh
set -euo pipefail
DB=${DB:-diario_test}
psql -v ON_ERROR_STOP=1 -q -c "drop database if exists $DB" -c "create database $DB"
psql -v ON_ERROR_STOP=1 -q -d "$DB" -f "$(dirname "$0")/supabase_mock.sql"
psql -v ON_ERROR_STOP=1 -q -d "$DB" -f "$(dirname "$0")/../supabase/setup.sql"
psql -v ON_ERROR_STOP=1 -q -d "$DB" -f "$(dirname "$0")/../supabase/setup.sql"          # 2ª vez: garante idempotência
psql -v ON_ERROR_STOP=1 -d "$DB" -f "$(dirname "$0")/test_setup_sql.sql" 2>&1 | grep -E "NOTICE|ERROR|PASSARAM" | sed 's/^psql:[^ ]* //'
