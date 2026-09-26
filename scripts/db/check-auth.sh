#!/bin/sh
set -eu
# Avoid cross-version PowerShell/native-command quoting and never print the password.
PGPASSWORD=$(cat /run/secrets/secretary_db_password)
export PGPASSWORD
exec psql -X -h 127.0.0.1 -U secretary_admin -d secretary -v ON_ERROR_STOP=1 \
    -c 'SELECT current_database(), current_user;'
