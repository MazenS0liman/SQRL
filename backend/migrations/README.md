# Database migrations

SQRL uses Alembic for schema changes. The existing services still create their
tables idempotently on first use, so the app works without migrations; new
tables and future changes should always land as a revision here.

```bash
cd backend
alembic upgrade head
alembic revision -m "describe the change"
alembic downgrade -1
```

The database URL is read from `DATABASE_URL` (or the legacy `POSTGRES_DSN`)
through `squirrel.core.config`, so no credentials are stored in
`alembic.ini`.
