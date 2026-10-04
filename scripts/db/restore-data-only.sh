#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: restore-data-only.sh <database> <archive>" >&2
    exit 2
fi

database=$1
archive=$2

case "$database" in
    secretary) ;;
    secretary_rebuild_*)
        suffix=${database#secretary_rebuild_}
        test -n "$suffix"
        test ${#database} -le 63
        case "$suffix" in *[!a-z0-9_]*) exit 2;; esac
        ;;
    *) exit 2 ;;
esac

if [ ! -f "$archive" ]; then
    echo "restore archive is missing: $archive" >&2
    exit 2
fi

toc=$(mktemp /tmp/lsa-restore-toc.XXXXXX)
filtered=$(mktemp /tmp/lsa-restore-filtered.XXXXXX)
trap 'rm -f "$toc" "$filtered"' EXIT HUP INT TERM

pg_restore --list "$archive" > "$toc"

migration_rows=$(grep -Ec ' TABLE DATA secretary schema_migrations ' "$toc" || true)
if [ "$migration_rows" -ne 1 ]; then
    echo "expected exactly one schema_migrations TABLE DATA entry, found $migration_rows" >&2
    exit 1
fi

awk '!/ TABLE DATA secretary schema_migrations /' "$toc" > "$filtered"

pg_restore \
    -U secretary_admin \
    -d "$database" \
    --data-only \
    --disable-triggers \
    --exit-on-error \
    --no-owner \
    --no-acl \
    --use-list="$filtered" \
    "$archive"
