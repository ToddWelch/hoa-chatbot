# Phase Notes

One section per phase. Brief and factual, no narration.

---

## Phase 1: Skeleton and DB

### Built

- Project root: `requirements.txt`, `.env.example`, `.gitignore`, `Procfile`, `Dockerfile`, `README.md` (full), `app.py` (factory + error handlers + CSRF wiring + scheduler boot + EMBEDDING_DIM validation).
- `db/`: `connection.py` (WAL + dict rows + 3-retry locked-backoff + transaction context manager), `migrations.py` (idempotent runner with `applied_migrations` ledger), `migrations/0001_init.sql` (10 tables), `migrations/README.md`.
- `services/`: `crypto.py` (Fernet), `config_store.py` (key-value table wrapper with key constants), `admin_auth.py` (bcrypt + IP hashing), `embeddings.py` (stub for Phase 3 with `EMBEDDING_DIM=512`, `validate_existing_chunks`), `csrf.py` (full hand-rolled CSRF), `gmail_ingest.py` (Phase 6 stub).
- `routes/`: `public.py` (with `/healthz`), `admin.py` (blueprint stub).
- `scheduler.py` (full; jobs registered for Phase 7).
- `scripts/init_db.py` (idempotent migrations + seed config + optional `--seed-addresses`).
- `scripts/set_admin_password.py` (interactive + `--password`).
- `scripts/start.py` (migrations -> `os.execvp("gunicorn", ...)`).
- Templates: `base.html`, `errors/{404,429,500,csrf_403}.html`.
- `static/style.css`.

### Decisions

- `DATABASE_PATH` defaults to `./hoa_bot.db` locally and `/data/hoa_bot.db` on Railway. Distinct from `DATA_DIR` so a local dev can put the DB outside `data/`.
- `_ensure_db_ready` in `app.py` runs migrations on factory boot; redundant with `scripts/start.py` but ensures a missed `init_db` cannot prevent the app from coming up clean. Idempotent.
- `services/csrf.py` is wired in Phase 1 (the context processor and decorator are imported by the factory) so later phases can apply `@require_csrf` without retroactively threading the wiring through.
- `services/embeddings.py` validates BLOB length even though the table is empty in Phase 1; the no-op early-return covers the fresh-DB case.
- Scheduler jobs are full Phase 7 implementations registered now; the gmail ingest job calls the Phase 6 stub which returns an empty result. Wiring is complete; Phase 6 fills in the body.

### Limitations

- Phase 1 deliverable is the bootable skeleton. The address gate, chat, admin login, and document upload come in later phases.
- `routes/public.py` only exposes `/healthz`. `/`, `/gate`, `/chat`, `/api/chat` arrive in Phase 2/3.

