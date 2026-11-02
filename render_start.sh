#!/usr/bin/env bash
# Starts the API on Render: update the database, make sure the administrator exists, then serve.
set -e
python manage.py migrate --noinput
if [ -n "$GREENDOC_ADMIN_EMAIL" ] && [ -n "$GREENDOC_ADMIN_PASSWORD" ]; then
  python manage.py seed_admin --email "$GREENDOC_ADMIN_EMAIL" \
    --first-name "${GREENDOC_ADMIN_FIRST_NAME:-Admin}" --last-name "${GREENDOC_ADMIN_LAST_NAME:-User}"
fi
# One worker: login lockouts and rate limits are counted in memory.
exec gunicorn greendoc.wsgi --bind "0.0.0.0:${PORT:-8000}" --workers 1 --threads 4 --timeout 300
