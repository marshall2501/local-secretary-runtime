#!/bin/sh
set -eu
# One psql session/transaction: lock serializes concurrent runners, including first use.
script=$(mktemp /tmp/secretary-migrate.XXXXXX)
trap 'rm -f "$script"' EXIT HUP INT TERM
cat > "$script" <<'SQL'
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(741392, 1);
CREATE SCHEMA IF NOT EXISTS secretary;
CREATE TABLE IF NOT EXISTS secretary.schema_migrations (
    version text PRIMARY KEY,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
);
SQL
for migration in /opt/secretary/migrations/[0-9][0-9][0-9]_*.sql; do
    test -f "$migration"
    version=$(basename "$migration")
    checksum=$(sha256sum "$migration" | cut -d ' ' -f 1)
    # Only checked-in filenames matching the restricted alphabet enter SQL.
    case "$version" in *[!a-zA-Z0-9_.-]*) exit 1;; esac
    cat >> "$script" <<SQL
SELECT EXISTS (SELECT 1 FROM secretary.schema_migrations WHERE version = '$version') AS applied,
       EXISTS (SELECT 1 FROM secretary.schema_migrations WHERE version = '$version' AND sha256 <> '$checksum') AS changed \gset
\if :changed
  DO \$\$ BEGIN RAISE EXCEPTION 'Applied migration checksum mismatch: $version'; END \$\$;
\endif
\if :applied
  \echo Already applied: $version
\else
  \i $migration
  INSERT INTO secretary.schema_migrations(version, sha256) VALUES ('$version', '$checksum');
\endif
SQL
done
printf '\nCOMMIT;\n' >> "$script"
psql -X -U secretary_admin -d secretary -f "$script"
