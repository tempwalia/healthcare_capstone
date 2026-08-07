#!/bin/sh
# Container entrypoint: migrate -> seed roles/permissions -> serve. Both
# steps are idempotent (alembic tracks its own version table, seed_roles.py
# checks by name before inserting - see app/core/seed.py), so this is safe
# to run on every container start, not just the first one.
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Seeding roles and permissions..."
python scripts/seed_roles.py

echo "Starting server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
