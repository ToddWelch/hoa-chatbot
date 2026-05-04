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

---

## Phase 2: Address gate and chat UI shell

### Built

- `services/address_normalize.py`: brief-spec pipeline (lowercase, drop periods, strip apt/unit, collapse whitespace, expand abbreviations). "way" stays as "way" per binding-scope item 14.
- `services/address_store.py`: DB-backed whitelist (plan-stage decision 1). Functions: `is_valid`, `add`, `list_all`, `remove`, `count`.
- `services/mailto.py`: safe mailto URI builder (binding-scope item 15). Strips CR/LF before encoding, caps subject 78, body 1800 with truncation suffix, URL-encodes via `quote(safe="")`.
- `services/rate_limit.py`: chat (50/24h) + admin login (5/15min). Both pairs functional now; admin path wired in Phase 4.
- `routes/public.py`: `/`, `/gate`, `/gate/failed`, `/logout`, `/chat`, plus the `/api/chat` Phase 3 handler scaffold (returns 200 with friendly fallback shape from chat service).
- Templates: `address_gate.html`, `address_failed.html`, `chat.html`.
- `static/chat.js`: vanilla JS POST to `/api/chat` with `X-CSRF-Token` header from meta tag. Renders user/assistant bubbles. Renders fallback messages with embedded mailto button.

### Decisions

- The 30-day chat session is set via `session.permanent = True` ONLY on successful gate POST (binding-scope item 9). Admin login does not touch this flag.
- `routes/public.py` API handler is included in Phase 2 so the route table is complete; it imports `services.chat` lazily so Phase 1/2 boot does not require Anthropic + Voyage clients.
- `services/address_store.py::add` swallows `IntegrityError` on UNIQUE conflict and returns False; callers can use the return value to flash "already in whitelist" feedback.
- The failed-address logging path uses `services.admin_auth.hash_ip` (SHA-256), not the raw IP, per the threat-model note (no PII beyond what the gate requires).

### Limitations

- The chat API handler (`/api/chat`) imports `services.chat` lazily; Phase 3 fills in that module.
- `/admin/failed-addresses` UI not yet wired (Phase 5); the data is logged correctly already.

### Manual verification

- `python scripts/init_db.py` produced 10 tables + sqlite_sequence.
- Address normalization checked against six test cases (incl. "way" preservation, apt/unit strip, abbreviation expansion).
- Flask test client: GET / returns gate page with CSRF meta tag; POST /gate with seeded address sets session and redirects to /chat; POST /gate with unknown address logs to failed_address_attempts and redirects to /gate/failed; POST /gate without CSRF returns 403.

