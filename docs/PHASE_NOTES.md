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

---

## Phase 3: Embeddings, retrieval, chat completion

### Built

- `services/embeddings.py`: full Voyage HTTP client (httpx). `embed_texts`, `embed_one`, `serialize`, `deserialize`, `validate_existing_chunks`. `EmbeddingsUnavailable` umbrella exception. Module-level `EMBEDDING_DIM=512`. Batched at 128 inputs/request.
- `services/retrieval.py`: cosine top-k over the chunks table joined with active documents only. `chunk_count` helper for the empty-table check.
- `services/chat.py`: `handle_chat` orchestrates rate-limit gate -> empty-chunks check -> Voyage retrieval -> Anthropic call -> persist messages + record rate-limit row in one transaction. Exception subclasses: `ChatRateLimited`, `ChatNoChunks`, `ChatServiceUnavailable`. Anthropic call uses the official SDK; model id from `ANTHROPIC_MODEL` env (default `claude-haiku-4-5`).
- `routes/public.py::api_chat` (built in Phase 2 stub, fully wired now): catches each chat exception subclass and returns the matching JSON shape (always 200 to the browser, never 500). Mailto fallback URI built per binding-scope item 15.

### Decisions

- System prompt anchors the model to "the context below" and instructs it to cite document titles and decline gracefully when the answer is not in the documents (per brief 6.2 and 6.6). Community name pulled from `config.community_name`.
- The transaction wrapping persistence + rate-limit record means a partial failure doesn't leave the rate-limit row without the conversation row (or vice versa).
- Cookie history (last 6 message pairs = 12 entries) is the hot cache; the conversations + messages tables are the source of truth. Browser-side: chat.js only sends the new message; the route reads history out of the session cookie.

### Limitations

- ANN indexing not implemented; full table scan over chunks. Plan deferred to v2 if chunks > ~50k. v1 expects ~3000.
- Anthropic chat-completion roundtrip with real model output requires live API keys (Phase 8 verification).
- Voyage embedding of real text requires live API keys (Phase 8 verification).

### Manual verification (with placeholder/invalid API keys)

- Gate to `/chat` works; `/api/chat` POST without CSRF returns 403.
- Empty-chunks fallback: with no chunks in the table, `/api/chat` returns `{"kind": "setup_in_progress", ...}` with mailto link, status 200.
- Service-unavailable fallback: with one chunk inserted and an invalid `VOYAGE_API_KEY`, `/api/chat` returns `{"kind": "service_unavailable", ...}` with mailto link, status 200. The 401 from Voyage is logged but never escapes as a 500.
- Rate-limit boundary: 0/49/50 inserts -> check_chat_rate returns True/True/False. The 51st chat triggers the rate-limit fallback in the route layer.

