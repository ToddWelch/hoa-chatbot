# HOA Chatbot, Implementation Plan (v1)

Status: draft for Crash Override review. After CO blesses this plan, Trinity executes the build straight through (one branch, single shot, per Todd's "build it all" directive).

This plan is the binding contract. The handshake at build time will quote the binding scope from this document verbatim.

---

## 1. Code read

Citations of every file consulted, with line ranges and what was learned. Required first by Trinity protocol.

### Build brief (the spec)

- `/home/todd/projects/hoa-chatbot/BUILD_BRIEF.md` 1 to 512 (entire file). Read end to end. Key sections:
  - Lines 1 to 22: latitude decisions list, ten items the orchestrator already exercised.
  - Lines 24 to 41: project identity, threat model. Single-tenant, single-board admin. Not multi-HOA.
  - Lines 43 to 60: stack table. Python 3.11, Flask 3.x, gunicorn 1 worker, SQLite + WAL, hand-written SQL migrations, APScheduler in-process, Voyage embeddings + numpy cosine, Anthropic Haiku, Fernet encryption, bcrypt admin auth, Gmail API + OAuth refresh token, server-rendered Jinja, pypdf.
  - Lines 64 to 78: cost estimate table, $5 to $10/mo with `COST FLAG:` watchpoints.
  - Lines 84 to 138: ten core feature blocks. Address gate normalization regex and abbreviation map at line 86. Empty-chunks fallback at line 95. Rate limit 50/24h at line 97. Mailto safety constraints at lines 99 to 103. Admin trust boundary at lines 104 to 113. Document ingestion 800-token chunks + 100-token overlap at line 116. Vector retrieval top K=6 at line 119. Gmail filter `is:unread -label:hoa-bot-processed from:<whitelisted_senders>` at line 124. Refresh token in `config.gmail_refresh_token_encrypted` at line 129.
  - Lines 142 to 235: full SQL schema. Eight tables (documents, chunks, conversations, messages, chat_rate_log, failed_address_attempts, admin_login_attempts, config). All FKs use ON DELETE CASCADE. IPs hashed only.
  - Lines 244 to 309: file structure diagram. Notes that `services/gmail_ingest.py` may split into auth.py + fetch.py + ingest.py if it crosses 300 LOC. Notes that `routes/admin.py` may split into per-area files.
  - Lines 314 to 361: env vars and gitignore.
  - Lines 365 to 425: phased build order, 8 phases.
  - Lines 446 to 475: operational notes (volume mount, manual backup, Gmail OAuth bootstrap, deploy steps, worker pinning).
  - Lines 498 to 508: open decisions for build time (Anthropic model string, Voyage model + dim, address list source, etc).

### WCC reference (closest analog codebase)

- WCC reference codebase, Flask app factory + APScheduler init module. Reviewed for: blueprint registration block, APScheduler integration, env-var skip flag pattern (`FLASK_SKIP_SCHEDULER`) for scripts that should not start the scheduler, and APScheduler logger routing to stdout. WCC uses Postgres + SQLAlchemy + Flask-Migrate; HOA project deviates to SQLite + raw sqlite3 + hand-written migrations. APScheduler pattern is directly reusable: `BackgroundScheduler` constructed inside the app factory, jobs registered with `scheduler.add_job(func, "cron"|"interval", ...)`, `scheduler.start()` called once.
- WCC reference codebase, ORM model with Fernet-encrypted columns. Reviewed for the wrapper pattern: module-private `_get_fernet()` reads `ENCRYPTION_KEY` from env and raises `RuntimeError` if unset; `set_<field>` / `get_<field>` instance methods wrap encrypt/decrypt around the persisted column; `to_dict()` returns `has_<field>` booleans, never plaintext. HOA project will follow this pattern in `services/crypto.py` plus `services/config_store.py` rather than per-model methods, because the only encrypted values live in the `config` key-value table.
- WCC reference codebase, Fernet helper module (mcp-gmail). Cleaner standalone Fernet wrapper. Module-level `encrypt(plaintext, key)` and `decrypt(ciphertext, key)` functions. CryptoError exception type wraps `cryptography.fernet.InvalidToken`. This is the better pattern for HOA project since secrets are not bound to a SQLAlchemy model. HOA project's `services/crypto.py` will follow this exact structure.
- WCC reference codebase, auth API module. Bcrypt auth pattern. Model-level `set_password` / `check_password` use `bcrypt.generate_password_hash(password).decode("utf-8")` and `bcrypt.check_password_hash(hash, password)`. HOA project does not have a User model (single admin, password hash in `config` key-value row), so the pattern collapses into two helper functions in `services/admin_auth.py`.
- WCC reference codebase, User model. The two-line bcrypt set/check pattern. Confirmed HOA project will use `flask-bcrypt`'s `Bcrypt()` extension or call `bcrypt.hashpw` / `bcrypt.checkpw` directly. Choosing direct `bcrypt` library because we have no User model and no need for Flask-Bcrypt's extension lifecycle; one less dependency.
- WCC reference codebase, embeddings service module. Module-level constants `_MODEL`, `_DIMS`, `_BATCH_MAX`, `_TIMEOUT`. Custom exception `EmbeddingsUnavailable` used to bubble all failure modes (decrypt failure, missing key, HTTP non-200, dim mismatch) into a single catch surface for callers. `embed_one` wraps `embed_batch` to keep error handling in one place. HOA project's `services/embeddings.py` adopts the pattern: module-level `EMBEDDING_DIM` constant, single exception type, `embed_one` and `embed_texts` entry points.
- WCC reference codebase, start script. Run migrations, then `os.execvp("gunicorn", ...)` to replace the process. WCC uses `flask db upgrade`; HOA uses `python scripts/init_db.py` which scans `db/migrations/*.sql` and applies un-applied files. The exec pattern is reusable. WCC's worker config `--workers 1 --threads 4 --timeout 120` is the right shape; HOA brief explicitly specifies `--workers 1 --timeout 120`. We add `--threads 4` to absorb concurrent chat requests without spawning workers (Python GIL is fine for I/O-bound work like waiting on Anthropic).
- WCC reference codebase, Dockerfile. Two-stage build (Node frontend + Python backend). HOA project has no React, so it collapses to a single Python stage. CMD calls `start.py`, which wraps migrations + exec gunicorn. HOA will mirror this single-Dockerfile, single-CMD pattern.
- WCC reference codebase, backend `requirements.txt`. Pin format reference. HOA's pin list will mirror exact-version pinning (`Package==X.Y.Z`).
- WCC reference codebase, Click-based CLI commands module. `@app.cli.command` pattern. HOA project uses standalone `python scripts/<name>.py` invocations rather than Flask CLI commands because the scripts run before the app factory is ready (init_db) or in restricted contexts (railway run). Direct `if __name__ == "__main__":` blocks are simpler. The `_ensure_encryption_key()` warning helper is a useful safety net for local dev; HOA project mirrors it in `scripts/init_db.py`.

### Outcome of the read

- WCC's app factory shape transfers directly. The blueprint registration block, APScheduler boot, and logging configuration are reusable.
- WCC's Fernet pattern collapses to a single module-level helper for HOA because all secrets live in one key-value table.
- WCC's bcrypt pattern collapses to two free functions for HOA because there is one admin password, not a User model.
- WCC's embeddings service pattern transfers directly with one constant change (1536 -> Voyage's chosen dim).
- WCC's startup script is the model for HOA's `scripts/init_db.py` -> `gunicorn` exec chain, but with hand-written `.sql` migrations in place of `flask db upgrade`.

---

## 2. New symbols introduced

One bullet per net-new function, class, route, table, helper. Module::symbol notation. Tables also listed.

### Tables (per schema in section 7 of the brief)

- `documents`
- `chunks`
- `conversations`
- `messages`
- `chat_rate_log`
- `failed_address_attempts`
- `admin_login_attempts`
- `config`
- `addresses` (NEW, decided in plan section 5 to replace `data/valid_addresses.txt`)
- `applied_migrations` (tracks which `.sql` files have been applied; idempotent reruns)

### Modules and symbols

`app.py`
- `app::create_app() -> Flask` (factory)
- `app::_register_blueprints(app)` (helper)
- `app::_register_error_handlers(app)` (handler for 404, 429, 500, generic Exception)
- `app::_configure_logging(app)`

`scheduler.py`
- `scheduler::start_scheduler(app) -> BackgroundScheduler`
- `scheduler::_job_gmail_ingest(app)` (wrapper that opens app context and calls `services.gmail_ingest.run_once`)
- `scheduler::_job_retention_sweep(app)` (deletes old `messages`, `chat_rate_log`, optionally `failed_address_attempts`)

`db/connection.py`
- `db.connection::get_db() -> sqlite3.Connection` (per-request connection, attached to Flask `g`)
- `db.connection::close_db(error=None)` (teardown)
- `db.connection::execute_with_retry(conn, sql, params=None)` (3 retries on `OperationalError: database is locked`, backoff 100/250/500ms)
- `db.connection::executemany_with_retry(conn, sql, seq)`
- `db.connection::row_factory_dict(cursor, row)` (returns dicts instead of sqlite3.Row tuples)

`db/migrations.py` (new file beyond brief; needed for the migration runner)
- `db.migrations::apply_pending(conn)` (scans `db/migrations/*.sql`, applies un-applied)
- `db.migrations::_ensure_applied_table(conn)`
- `db.migrations::_record_applied(conn, filename)`

`db/migrations/0001_init.sql` (the schema file)

`routes/__init__.py` (empty stub for package)

`routes/public.py`
- `public_bp = Blueprint("public", __name__)`
- `public_bp::index()` GET `/` (renders address_gate.html if not gated, else redirect to /chat)
- `public_bp::gate_submit()` POST `/gate`
- `public_bp::gate_failed()` GET `/gate/failed`
- `public_bp::chat_view()` GET `/chat` (gated)
- `public_bp::api_chat()` POST `/api/chat` (JSON in/out, gated)
- `public_bp::_require_chat_session()` (decorator)

`routes/admin.py`
- `admin_bp = Blueprint("admin", __name__, url_prefix="/admin")`
- `admin_bp::login_view()` GET `/admin/login`
- `admin_bp::login_submit()` POST `/admin/login`
- `admin_bp::logout()` POST `/admin/logout`
- `admin_bp::dashboard()` GET `/admin/dashboard`
- `admin_bp::documents_list()` GET `/admin/documents`
- `admin_bp::documents_upload()` POST `/admin/documents/upload`
- `admin_bp::documents_paste()` POST `/admin/documents/paste`
- `admin_bp::document_toggle_active(doc_id)` POST `/admin/documents/<int:doc_id>/toggle`
- `admin_bp::document_reembed(doc_id)` POST `/admin/documents/<int:doc_id>/reembed`
- `admin_bp::conversations_list()` GET `/admin/conversations`
- `admin_bp::conversation_view(conv_id)` GET `/admin/conversations/<int:conv_id>`
- `admin_bp::failed_addresses_list()` GET `/admin/failed-addresses`
- `admin_bp::failed_address_to_whitelist()` POST `/admin/failed-addresses/whitelist`
- `admin_bp::config_view()` GET `/admin/config`
- `admin_bp::config_update_email()` POST `/admin/config/email`
- `admin_bp::config_change_password()` POST `/admin/config/password`
- `admin_bp::gmail_test_run()` POST `/admin/config/gmail/test-run`
- `admin_bp::_require_admin()` (decorator)

`services/address_normalize.py`
- `services.address_normalize::normalize(raw: str) -> str`
- `services.address_normalize::ABBREVIATIONS: dict[str, str]` (the brief's expansion map)
- `services.address_normalize::APT_UNIT_PATTERN: re.Pattern`

`services/embeddings.py`
- `services.embeddings::EMBEDDING_DIM: int` (module constant; whatever Voyage's `voyage-3-lite` returns; confirmed at build time)
- `services.embeddings::EmbeddingsUnavailable(Exception)`
- `services.embeddings::embed_texts(texts: list[str]) -> list[np.ndarray]`
- `services.embeddings::embed_one(text: str) -> np.ndarray`
- `services.embeddings::serialize(vec: np.ndarray) -> bytes` (writes `.tobytes()` and validates dtype/dim)
- `services.embeddings::deserialize(blob: bytes) -> np.ndarray` (reads back)
- `services.embeddings::validate_existing_chunks(conn)` (boot-time check that any persisted chunk's BLOB length matches the current `EMBEDDING_DIM`)

`services/retrieval.py` (split out per plan-stage decision 3)
- `services.retrieval::top_k(conn, query: str, k: int = 6) -> list[dict]`
- `services.retrieval::_cosine_search(query_vec: np.ndarray, chunks: list[dict]) -> list[tuple[float, dict]]`

`services/chat.py`
- `services.chat::ANTHROPIC_MODEL: str` (module constant)
- `services.chat::ChatServiceError(Exception)` (umbrella; subclasses below)
- `services.chat::ChatServiceUnavailable(ChatServiceError)`
- `services.chat::ChatRateLimited(ChatServiceError)`
- `services.chat::ChatNoChunks(ChatServiceError)`
- `services.chat::handle_chat(conn, normalized_address: str, user_msg: str, history: list[dict]) -> dict` (orchestrates rate-limit check, retrieval, Anthropic call, fallback messages)
- `services.chat::_build_system_prompt(chunks: list[dict]) -> str`
- `services.chat::_call_anthropic(system_prompt: str, messages: list[dict]) -> str`

`services/documents.py`
- `services.documents::CHUNK_TOKENS: int = 800`
- `services.documents::CHUNK_OVERLAP_TOKENS: int = 100`
- `services.documents::OCR_NEEDED_THRESHOLD_BYTES: int = 100_000`
- `services.documents::OCR_NEEDED_MIN_CHARS: int = 50`
- `services.documents::ingest_pdf_bytes(conn, title: str, file_bytes: bytes, source: str, gmail_message_id: str | None = None) -> int` (returns doc_id)
- `services.documents::ingest_text(conn, title: str, full_text: str, source: str, gmail_message_id: str | None = None) -> int`
- `services.documents::reembed_document(conn, doc_id: int)`
- `services.documents::soft_delete(conn, doc_id: int)`
- `services.documents::_extract_pdf_text(file_bytes: bytes) -> str`
- `services.documents::_chunk_text(text: str) -> list[str]`
- `services.documents::_save_pdf_to_volume(file_bytes: bytes, doc_id: int) -> str` (returns relative path)
- `services.documents::_compute_file_hash(file_bytes: bytes) -> str`

`services/gmail_ingest.py`
- `services.gmail_ingest::GmailIngestError(Exception)`
- `services.gmail_ingest::run_once(conn, app_logger) -> dict` (returns `{ "fetched": N, "ingested": M, "skipped_dedup": K, "errors": [...] }`)
- `services.gmail_ingest::_get_gmail_service()` (builds the Google API client from refresh token decrypted from `config`)
- `services.gmail_ingest::_load_whitelisted_senders() -> list[str]`
- `services.gmail_ingest::_search_query(senders: list[str]) -> str`
- `services.gmail_ingest::_extract_message(message_dict) -> dict` (subject, body text, attachments)
- `services.gmail_ingest::_apply_processed_label(service, message_id)`
- `services.gmail_ingest::_ensure_processed_label_id(service) -> str` (creates `hoa-bot-processed` label if missing)

If this file approaches 300 LOC during build, it will split into `services/gmail_auth.py`, `services/gmail_fetch.py`, `services/gmail_ingest.py` per the brief's note. The split is identified ahead of time by Trinity's deviation protocol; if the split happens, it is recorded in the build report.

`services/crypto.py`
- `services.crypto::CryptoError(Exception)`
- `services.crypto::encrypt(plaintext: str) -> str` (returns Fernet token as ASCII string)
- `services.crypto::decrypt(ciphertext: str) -> str`
- `services.crypto::_get_fernet() -> Fernet` (reads `ENCRYPTION_KEY` env, raises if unset)

`services/config_store.py` (new helper not explicit in brief; replaces ad-hoc `config` table reads scattered across modules)
- `services.config_store::get(conn, key: str, default: str | None = None) -> str | None` (plaintext)
- `services.config_store::set(conn, key: str, value: str)` (plaintext)
- `services.config_store::get_secret(conn, key: str) -> str | None` (decrypts)
- `services.config_store::set_secret(conn, key: str, value: str)` (encrypts)
- `services.config_store::has(conn, key: str) -> bool`

`services/admin_auth.py` (split from `services/rate_limit.py` so password hashing and IP rate limiting do not share a file)
- `services.admin_auth::hash_password(plaintext: str) -> str`
- `services.admin_auth::verify_password(plaintext: str, hashed: str) -> bool`
- `services.admin_auth::hash_ip(remote_addr: str) -> str` (SHA-256 hex)

`services/csrf.py`
- `services.csrf::generate_token() -> str` (returns `secrets.token_urlsafe(32)`)
- `services.csrf::ensure_session_token() -> str` (lazily sets `session["csrf_token"]` if missing, returns the token; persisted across POSTs so the same token works through a multi-step flow)
- `services.csrf::validate(submitted: str | None) -> bool` (uses `hmac.compare_digest(submitted or "", session.get("csrf_token", ""))`, never `==`, to avoid timing leaks)
- `services.csrf::require_csrf` (decorator factory: reads `request.form.get("csrf_token")` for HTML POSTs and `request.headers.get("X-CSRF-Token")` for JSON POSTs, calls `validate`, `abort(403)` rendering `templates/errors/csrf_403.html` on failure)
- `services.csrf::inject_csrf_token()` (Jinja context processor returning `{"csrf_token": ensure_session_token()}` so every template can render `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">` and chat.html can render `<meta name="csrf-token" content="{{ csrf_token }}">`)

`services/rate_limit.py`
- `services.rate_limit::check_chat_rate(conn, normalized_address: str, limit: int = 50, window_hours: int = 24) -> bool`
- `services.rate_limit::record_chat(conn, normalized_address: str)`
- `services.rate_limit::check_admin_login(conn, ip_hash: str, limit: int = 5, window_minutes: int = 15) -> bool`
- `services.rate_limit::record_admin_login(conn, ip_hash: str, succeeded: bool)`

`services/mailto.py`
- `services.mailto::SUBJECT_MAX: int = 78`
- `services.mailto::BODY_MAX: int = 1800`
- `services.mailto::BODY_TRUNCATION_SUFFIX: str = " (message truncated, please paste from chat)"`
- `services.mailto::build_mailto(to: str, subject: str, body: str) -> str`

`services/address_store.py` (new, decided in plan section 5)
- `services.address_store::is_valid(conn, normalized: str) -> bool`
- `services.address_store::add(conn, normalized: str, source: str = "admin", note: str | None = None)`
- `services.address_store::list_all(conn) -> list[dict]`

`scripts/init_db.py`
- `scripts.init_db::main()`

`scripts/set_admin_password.py`
- `scripts.set_admin_password::main()`

`scripts/gmail_oauth_setup.py`
- `scripts.gmail_oauth_setup::main()`

`templates/` (Jinja files; no symbols, listed in section 6)

`static/chat.js` (vanilla JS, no symbols)

### Routes summary

Public:
- GET `/`
- POST `/gate`
- GET `/gate/failed`
- GET `/chat`
- POST `/api/chat`

Admin:
- GET `/admin/login`
- POST `/admin/login`
- POST `/admin/logout`
- GET `/admin/dashboard`
- GET `/admin/documents`
- POST `/admin/documents/upload`
- POST `/admin/documents/paste`
- POST `/admin/documents/<doc_id>/toggle`
- POST `/admin/documents/<doc_id>/reembed`
- GET `/admin/conversations`
- GET `/admin/conversations/<conv_id>`
- GET `/admin/failed-addresses`
- POST `/admin/failed-addresses/whitelist`
- GET `/admin/config`
- POST `/admin/config/email`
- POST `/admin/config/password`
- POST `/admin/config/gmail/test-run`

---

## 3. Existing symbols modified

None. This is a greenfield build.

---

## 4. Plan-stage decisions

The ten items the orchestrator surfaced in the dispatch, each resolved with the chosen path and a one-sentence rationale.

1. **Whitelist storage location.**
   - Decision: DB table (`addresses`) with one row per normalized address.
   - Rationale: the failed-address admin flow appends at runtime, which a flat file makes painful; a DB row keeps everything inside one transaction and removes the file-load-on-startup race. Schema below.

   ```sql
   CREATE TABLE addresses (
     id INTEGER PRIMARY KEY AUTOINCREMENT,
     normalized_address TEXT NOT NULL UNIQUE,
     raw_address TEXT,                       -- original form, for admin display
     source TEXT NOT NULL,                   -- 'seed' | 'admin' | 'failed_promotion'
     note TEXT,
     created_at TEXT NOT NULL DEFAULT (datetime('now'))
   );
   CREATE INDEX idx_addresses_normalized ON addresses(normalized_address);
   ```

   `data/valid_addresses.example.txt` retained as a seed-source format example. `scripts/init_db.py` accepts an optional `--seed-addresses <path>` argument that imports a file at first deploy. After that, edits go through `/admin/failed-addresses` or a manual `/admin/addresses` page (plan adds a small `addresses` admin page in phase 5).

2. **Gmail token column naming.**
   - Decision: key = `gmail_refresh_token`, ciphertext stored in `value_encrypted` column of the `config` table. All references in code use the constant `services.config_store::KEY_GMAIL_REFRESH_TOKEN = "gmail_refresh_token"`.
   - Rationale: the brief had three different phrasings; CO chose this one and it matches the schema definition at brief line 232. The constant in `config_store` prevents typos at the use site.

3. **`services/chat.py` split.**
   - Decision: split into `services/retrieval.py` (cosine search over chunks) and `services/chat.py` (rate-limit gate, retrieval call, Anthropic call, fallback shaping).
   - Rationale: each file has one responsibility, keeps both under 200 LOC with comments, and lets a future caller (e.g. an admin "preview a query" tool) reuse retrieval without dragging in the chat layer.

4. **`chat_rate_log` retention.**
   - Decision: the daily retention sweep deletes `chat_rate_log` rows older than 48 hours.
   - Rationale: rolling window is 24 hours; 48 gives a safe buffer for clock skew and any time-of-day batching while keeping the table tiny.

5. **`ENCRYPTION_KEY` continuity.**
   - Decision: `scripts/init_db.py` and `scripts/gmail_oauth_setup.py` both call `services.crypto._get_fernet()` to validate the key shape on startup. README "Operations" section documents that key rotation breaks existing encrypted rows. `.env.example` includes a brief comment to that effect.
   - Rationale: silent loss of the Gmail token after a key rotation is a hard-to-debug failure mode; surfacing it in two places (script-level boot check + README) is cheap insurance.

6. **Embedding dimension as startup-checked constant.**
   - Decision: `services.embeddings::EMBEDDING_DIM` is a module constant. App factory calls `embeddings.validate_existing_chunks(conn)` once at boot; if any chunk's BLOB length does not equal `EMBEDDING_DIM * 4` (float32), raise `RuntimeError` and refuse to start. Re-embedding requires the admin to use `/admin/documents/<id>/reembed` after the dim change.
   - Rationale: a model swap that changes embedding dimension would otherwise corrupt retrieval at first inference; a boot-time check fails fast.

7. **Gmail OAuth bootstrap procedure.**
   - Decision: prefer `railway run python scripts/gmail_oauth_setup.py` against the prod database. README documents this as the primary path. The "run locally + copy DB row" path is removed from the operational notes.
   - Rationale: copying a DB row across environments is error-prone and was never necessary; `railway run` opens a process with prod env vars and prod volume mounted.
   - Operational caveat: the OAuth web flow needs a browser. `gmail_oauth_setup.py` will print a URL and wait for the operator to paste back the redirected code (manual flow); this works from a Railway shell because `railway run` proxies stdin. The script does not need a local web server.

8. **Dead sentence in section 6.8.**
   - Decision: ignore the "chat_log (legacy if used)" reference. Retention sweep operates on `messages` only (90 days) and `chat_rate_log` (48 hours). README "Retention" subsection states the rules explicitly so the next reader does not search for a `chat_log` table.
   - Rationale: there is no `chat_log` table and there never will be; removing the language from the implementation prevents a future builder from creating a stub table.

9. **Session continuity.**
   - Decision: `app.py` sets `app.permanent_session_lifetime = timedelta(days=30)`. On successful gate submit, `routes/public.py::gate_submit()` sets `session.permanent = True` (one-time, drives the 30-day chat-gate cookie). Admin login does NOT touch `session.permanent`; the admin idle timeout is enforced purely by a manual `last_admin_seen` timestamp.
   - Mechanism (admin idle, 1 hour): on successful admin login `routes/admin.py::login_submit()` sets `session["last_admin_seen"] = time.time()` along with the admin flag. Every call into `_require_admin()` reads `session.get("last_admin_seen", 0)`; if `time.time() - session.get("last_admin_seen", 0) > 3600`, it clears the admin flag (and the `last_admin_seen` key), flashes a "your admin session expired" message, and redirects to `/admin/login?reason=idle`. If the timeout has not expired, `_require_admin()` writes `session["last_admin_seen"] = time.time()` to slide the window on every protected request.
   - Coexistence with the 30-day chat session: `session.permanent` is set once at chat-gate success and is never set or cleared by the admin login or logout paths. The 30-day Flask permanent-session cookie continues to live independently. The 1-hour admin idle is enforced purely by the manual `last_admin_seen` check; it does not rely on `session.permanent` being False, and admin login does not change `session.permanent`.
   - Rationale: gate cookie should last 30 days per brief; admin cookie should idle out after 1 hour per brief. Mixing Flask's `permanent_session_lifetime` for two different timeouts on one session is brittle, so the admin path uses the manual-timestamp pattern instead.

10. **Admin password change flow.**
    - Decision: `scripts/set_admin_password.py` and `/admin/config/password` both call `services.admin_auth::hash_password(plaintext)` and write the result to `config.value` for `key='admin_password_hash'` via `services.config_store::set("admin_password_hash", hashed)`. Same code path, no duplication.
    - Rationale: bcrypt hash format must be byte-for-byte identical between bootstrap and runtime change; sharing the helper guarantees that.

---

## 5. File-by-file blueprint

Grouped by directory. LOC estimates exclude blank lines and comments. Each module respects the 300 LOC + two-responsibility cap; where a file approaches 300 LOC, the planned split is noted.

### Root

`app.py` (factory + registration only). ~120 LOC.
- Imports: `flask.Flask`, blueprint modules, `scheduler.start_scheduler`, env loader.
- `create_app() -> Flask`: build config, register blueprints, register error handlers, configure logging, validate `EMBEDDING_DIM` against existing chunks via a single connection, start scheduler unless `FLASK_SKIP_SCHEDULER=1`.
- `_register_error_handlers(app)`: 404 -> friendly page; 429 -> generic JSON for API and friendly page for HTML; 500 -> friendly page that renders the mailto fallback for chat or generic for admin; catch-all `Exception` for unexpected errors that logs and returns a friendly fallback.
- Dependencies: `routes/public.py`, `routes/admin.py`, `scheduler.py`, `db/connection.py`, `services/embeddings.py`.

`scheduler.py`. ~80 LOC.
- Imports: `apscheduler.schedulers.background.BackgroundScheduler`, the two job wrappers.
- `start_scheduler(app)`: build `BackgroundScheduler()`, register two jobs (gmail every 15 min, retention daily 03:00 UTC), `scheduler.start()`, attach to `app.apscheduler`. If `FLASK_SKIP_SCHEDULER` is set, return without starting.
- `_job_gmail_ingest(app)`: open app context, open DB conn, call `gmail_ingest.run_once`, log result, swallow exceptions with `logger.error(..., exc_info=True)`.
- `_job_retention_sweep(app)`: open app context, open DB conn, delete `messages` older than 90 days, delete orphan `conversations`, delete `chat_rate_log` older than 48 hours, log counts.
- Dependencies: `services/gmail_ingest.py`, `db/connection.py`.

`Procfile`. 1 line.
- `web: gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT app:create_app()`

`Dockerfile`. ~25 LOC.
- Base: `python:3.11-slim`.
- Install pip deps from `requirements.txt`.
- Copy app source.
- `EXPOSE 8080`.
- `CMD ["python", "scripts/start.py"]` (start.py runs migrations then exec gunicorn).

`scripts/start.py`. ~50 LOC.
- New file beyond brief's listed scripts. Mirrors WCC's `start.py` but simpler. Runs `python scripts/init_db.py` (which is idempotent), then `os.execvp("gunicorn", [...])` with the same args as the Procfile. Lets Railway use Dockerfile CMD. Also written as a Procfile alternative for local dev.

`requirements.txt`. ~15 lines (pinned).

`.env.example`. ~25 lines (per brief section 9). Includes a `DATA_DIR` entry: default `./data` for local development, set to `/data` in Railway env. Used by the config loader as the base directory for `whitelisted_senders.txt` and any other seed-only files. The `data/app.db` path is still controlled by `DATABASE_PATH` (kept distinct so a local dev can point the SQLite file somewhere outside `DATA_DIR` if desired). The `addresses` table is DB-backed and unaffected by `DATA_DIR`.

`.gitignore`. ~15 lines (per brief section 9).

`README.md`. ~150 LOC.
- Sections: Overview, Local development, Environment variables, Database initialization, Admin password, Gmail OAuth bootstrap, Deploying to Railway, Operations (backup, retention, key rotation), Out of scope.

### `routes/`

`routes/__init__.py`. Empty (package marker).

`routes/public.py`. ~250 LOC.
- Address gate, chat UI, chat API. Three routes plus the API endpoint plus a `_require_chat_session` decorator.
- Renders `address_gate.html`, `address_failed.html`, `chat.html`.
- API endpoint serializes JSON.
- CSRF posture for `/api/chat` (option a, header-based): the chat.html template renders `<meta name="csrf-token" content="{{ csrf_token }}">`, chat.js reads it on page load and sends it as the `X-CSRF-Token` header on every POST to `/api/chat`. The same `@require_csrf` decorator from `services/csrf.py` is applied to `/api/chat`; it reads the header (form field if present, otherwise `X-CSRF-Token`) and validates with `hmac.compare_digest`. The address-gate POST is also CSRF-protected via the hidden form input rendered by `inject_csrf_token`.
- Dependencies: `services/address_normalize.py`, `services/address_store.py`, `services/chat.py`, `services/csrf.py`, `services/mailto.py`, `services/rate_limit.py`, `db/connection.py`.

`routes/admin.py`. ~290 LOC. If it crosses 300 during build, split into `routes/admin/login.py`, `routes/admin/documents.py`, `routes/admin/conversations.py`, `routes/admin/config.py`, `routes/admin/failed_addresses.py` per the brief's note. The split point will be identified during the build at ~250 LOC; if hit, the deviation protocol surfaces it.
- All admin routes plus `_require_admin` decorator.
- CSRF: hand-rolled session-bound tokens via `services/csrf.py` (Flask-WTF avoided to keep dependency footprint small). Token generated by `secrets.token_urlsafe(32)`, stored in `session["csrf_token"]`, persisted across POSTs (the same token survives a full admin session). Jinja context processor `inject_csrf_token` renders the token into every template (hidden input on HTML forms, meta tag on chat.html). The `@require_csrf` decorator from `services/csrf.py` is applied to every admin POST and to `/api/chat`; it reads `request.form.get("csrf_token")` or `request.headers.get("X-CSRF-Token")` and validates with `hmac.compare_digest(submitted or "", session.get("csrf_token") or "")` (never `==`, to avoid timing leaks). On failure the decorator calls `abort(403)`, which the registered error handler renders as `templates/errors/csrf_403.html`.
- CSRF scope: every admin POST (login, logout, upload, paste, toggle, reembed, soft-delete, password change, email change, gmail test-run, failed-address whitelist promotion) plus the public `/api/chat` POST. The address-gate POST (`/gate`) is also CSRF-protected: rationale is that session-bound CSRF is cheap once the context processor is wired, and the gate POST writes a row to `failed_address_attempts` (and on success the residents-equivalent state) so it should not be CSRF-spoofable. The gate page renders the hidden token via the same context processor, so no extra plumbing is needed.
- Dependencies: `services/admin_auth.py`, `services/csrf.py`, `services/rate_limit.py`, `services/config_store.py`, `services/documents.py`, `services/address_store.py`, `services/gmail_ingest.py` (for the test-run button), `db/connection.py`.

### `services/`

`services/__init__.py`. Empty.

`services/address_normalize.py`. ~60 LOC.
- One public function `normalize`, the abbreviation map, the apt/unit pattern. Pure (no DB).

`services/address_store.py`. ~80 LOC.
- Reads/writes `addresses` table. Functions in section 2.

`services/embeddings.py`. ~150 LOC.
- Voyage HTTP client via `httpx`. Constants: `EMBEDDING_DIM`, `_VOYAGE_URL`, `_MODEL`, `_TIMEOUT=10`, `_BATCH_MAX`. Functions: `embed_texts`, `embed_one`, `serialize`, `deserialize`, `validate_existing_chunks`. `EmbeddingsUnavailable` exception.
- API key read from env `VOYAGE_API_KEY`. No DB lookup (Voyage key is not a rotated secret like Gmail's).

`services/retrieval.py`. ~70 LOC.
- `top_k(conn, query, k=6)`: embed query, fetch all active chunks (`is_active=1` join), cosine score in numpy, return top k as list of dicts including chunk text and parent document title.
- For v1 with ~3000 chunks the full scan is fine. ANN indexing deferred to v2 if chunks > ~50k.

`services/chat.py`. ~180 LOC.
- `handle_chat(conn, normalized_address, user_msg, history)`:
  1. `rate_limit.check_chat_rate(conn, normalized_address)`. If False, raise `ChatRateLimited`.
  2. Count active chunks. If zero, raise `ChatNoChunks`.
  3. Try `retrieval.top_k(conn, user_msg, 6)`; on Voyage error, raise `ChatServiceUnavailable`.
  4. Build system prompt with cited document titles.
  5. Call Anthropic Haiku with system prompt + history + user_msg; on Anthropic error, raise `ChatServiceUnavailable`.
  6. Record rate-limit row (`rate_limit.record_chat`).
  7. Return `{ "response": str, "cited_documents": [...] }`.
- Catches in the route handler render the appropriate fallback copy with the mailto link.
- Anthropic call uses official `anthropic` SDK; model id from `ANTHROPIC_MODEL` env (default `claude-haiku-4-5`, confirmed at build time per brief open decision 1).

`services/documents.py`. ~270 LOC.
- PDF parsing via pypdf, text chunking, embedding via `services.embeddings`, DB writes for `documents` and `chunks` tables. File-on-volume save for PDFs.
- Chunking: token-aware via simple whitespace approximation (1 token ~ 4 chars). True tokenizer not needed at v1 chunk boundaries; Voyage tokenizes on its end for embedding.
- If this file approaches 300 LOC during build, split into `services/document_parse.py` (PDF + chunk) and `services/document_store.py` (DB writes + volume save). Identified ahead of time; deviation protocol covers it.

`services/gmail_ingest.py`. ~280 LOC.
- Functions per section 2. Uses `google-api-python-client` and `google-auth`.
- `_load_whitelisted_senders()` reads `${DATA_DIR}/whitelisted_senders.txt` (path resolved from the `DATA_DIR` env var by the config loader; default `./data/whitelisted_senders.txt`, on Railway `/data/whitelisted_senders.txt`).
- If approaching 300 LOC during build, splits into `services/gmail_auth.py` (refresh token + service builder), `services/gmail_fetch.py` (search + extract), `services/gmail_ingest.py` (run_once orchestrator + dedup + label apply). Identified ahead of time per brief's note; deviation protocol covers it.

`services/crypto.py`. ~50 LOC.
- Mirrors mcp-gmail's pattern. `encrypt`, `decrypt`, `_get_fernet`, `CryptoError`.

`services/config_store.py`. ~80 LOC.
- Wraps `config` table reads/writes with optional encryption. Constants for known keys (`KEY_ADMIN_PASSWORD_HASH`, `KEY_HOA_CONTACT_EMAIL`, `KEY_GMAIL_REFRESH_TOKEN`, `KEY_GMAIL_OAUTH_CLIENT_ID`, `KEY_GMAIL_OAUTH_CLIENT_SECRET`).

`services/admin_auth.py`. ~40 LOC.
- bcrypt wrappers, IP hashing helper. Three pure functions.

`services/csrf.py`. ~60 LOC.
- Session-bound hand-rolled CSRF. Functions: `generate_token` (`secrets.token_urlsafe(32)`), `ensure_session_token` (lazy set on `session["csrf_token"]`, persisted across POSTs), `validate` (constant-time via `hmac.compare_digest`), `require_csrf` decorator (reads form field or `X-CSRF-Token` header, calls `abort(403)` on failure rendering `templates/errors/csrf_403.html`), `inject_csrf_token` Jinja context processor registered by the app factory so every rendered template (including `chat.html`'s meta tag) gets the token. Consumed by every admin POST, by `/api/chat`, and by the address gate POST.

`services/rate_limit.py`. ~80 LOC.
- Two pairs of functions: chat (50/24h per address) and admin login (5/15min per IP). Reads/writes `chat_rate_log` and `admin_login_attempts`.

`services/mailto.py`. ~70 LOC.
- `build_mailto`: strip CR/LF, cap subject at 78 chars, cap body at 1800 with truncation suffix, URL-encode via `urllib.parse.quote(safe='')`, return `mailto:<to>?subject=...&body=...`.

### `db/`

`db/__init__.py`. Empty.

`db/connection.py`. ~120 LOC.
- `get_db()`: per-request connection on Flask `g`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, dict row factory.
- `close_db`: registered as `app.teardown_appcontext`.
- `execute_with_retry`: wraps `cursor.execute` with three-attempt retry on `OperationalError: database is locked` with backoff 100/250/500ms. Same for `executemany_with_retry`.
- A small `transaction()` context manager wrapping BEGIN/COMMIT/ROLLBACK with the same retry semantics for the surrounding BEGIN.

`db/migrations.py`. ~70 LOC.
- `apply_pending(conn)`: ensure `applied_migrations` table exists; for each `db/migrations/*.sql` sorted ascending, if filename not in `applied_migrations`, run the file and record it.
- Print each applied filename to stdout for the deploy log.

`db/migrations/0001_init.sql`. ~120 LOC.
- All ten tables (eight from brief schema + `addresses` + `applied_migrations`) with constraints and indexes.

`db/migrations/README.md`. ~20 lines.
- One paragraph: hand-written SQL files, applied in filename order, idempotency via `applied_migrations`, never autogenerated.

### `templates/`

`templates/base.html`. ~40 LOC.
- Jinja base with header, content block, simple footer with mailto.

`templates/address_gate.html`. ~30 LOC.
- Form with one address field, CSRF token, submit button.

`templates/address_failed.html`. ~25 LOC.
- Friendly error with mailto link.

`templates/chat.html`. ~60 LOC.
- Chat UI: scrollable transcript area, input + submit. Includes `static/chat.js`.
- Renders `<meta name="csrf-token" content="{{ csrf_token }}">` in the `<head>` so chat.js can read the session-bound CSRF token and send it as the `X-CSRF-Token` header on every POST to `/api/chat`. The `csrf_token` value comes from the `inject_csrf_token` context processor registered in the app factory.

`templates/admin/login.html`. ~25 LOC.

`templates/admin/dashboard.html`. ~50 LOC.
- Cards: documents count, chunks count, chats today, chats 30d, failed-address attempts, OCR-needed PDFs, Gmail ingest status (last run + count). Buttons to documents / conversations / failed addresses / config.

`templates/admin/documents.html`. ~80 LOC.
- Upload form (PDF), paste form (title + textarea), table of documents with toggle/reembed/delete actions and "needs OCR" badge.

`templates/admin/conversations.html`. ~50 LOC.
- Table of recent conversations with view link.

`templates/admin/conversation_view.html`. ~40 LOC.
- Full transcript for one conversation.

`templates/admin/failed_addresses.html`. ~50 LOC.
- Table with "add to whitelist" action.

`templates/admin/addresses.html`. ~40 LOC.
- List of whitelisted addresses with a small add form. New page added per plan-stage decision 1.

`templates/admin/config.html`. ~70 LOC.
- HOA contact email form, change-password form, Gmail OAuth status panel with "test ingest" button.

### `static/`

`static/chat.js`. ~80 LOC.
- POST to `/api/chat`, append response to transcript, simple loading indicator. No build step. Uses `fetch`.
- On load, reads the CSRF token from the `<meta name="csrf-token">` tag rendered into chat.html and sends it as the `X-CSRF-Token` header on every POST. No token in the JSON body; header only.

`static/style.css`. ~120 LOC.
- Minimal styles. Mobile-friendly chat layout, admin tables.

### `scripts/`

`scripts/init_db.py`. ~80 LOC.
- Open SQLite at `DATABASE_PATH`, ensure parent dir exists, apply migrations, seed any missing config keys with placeholders (e.g. `hoa_contact_email='hoa@example.com'`). Optional `--seed-addresses <path>` reads a text file (one normalized address per line) and inserts into `addresses` table. Validates `ENCRYPTION_KEY` shape via `services.crypto._get_fernet()` so a bad key fails here, not at first OAuth. Idempotent.

`scripts/set_admin_password.py`. ~40 LOC.
- Reads from stdin (or `--password` arg for non-interactive). Calls `services.admin_auth.hash_password`, writes to `config.value` via `services.config_store.set`.

`scripts/gmail_oauth_setup.py`. ~120 LOC.
- Reads `GMAIL_OAUTH_CLIENT_ID` + `GMAIL_OAUTH_CLIENT_SECRET` from env. Builds Google's installed-app OAuth flow with `gmail.modify` scope. Prints authorization URL, waits for the operator to paste the code, exchanges code for refresh token, encrypts via `services.crypto.encrypt`, writes to `config.value_encrypted` via `services.config_store.set_secret`. Prints success.

`scripts/start.py`. (Already covered in Root.)

### `data/` (paths shown are local defaults; resolved via `DATA_DIR` env var)

- `${DATA_DIR}/documents/` (gitignored, on Railway volume in prod). Created by app on first PDF upload. Default `./data/documents/` locally, `/data/documents/` on Railway.
- `data/whitelisted_senders.example.txt` (in repo). ~5 lines of sample emails. Reference format only.
- `${DATA_DIR}/whitelisted_senders.txt`. (Gitignored.) Operator edits this file to whitelist senders. Loaded at every scheduler tick into the Gmail filter query. Default `./data/whitelisted_senders.txt` locally; on Railway it resolves to `/data/whitelisted_senders.txt`.
- `data/valid_addresses.example.txt` (in repo). ~5 lines of sample addresses. Real address list now lives in DB per plan-stage decision 1; this file is kept only as a seed-import format example. Read by `scripts/init_db.py --seed-addresses` from the path the operator passes (no `DATA_DIR` resolution needed here; it is a one-shot seed).
- `${DATA_DIR}/app.db`. (Gitignored.) SQLite file; lives on the Railway volume mount in prod. Path is set explicitly by `DATABASE_PATH` env var (which defaults to `${DATA_DIR}/app.db` if unset, but stays a separate variable so a local dev can override).

---

## 6. Database migration plan

Hand-written SQL, never autogenerated, per global rules. One file at `db/migrations/0001_init.sql`. Applied in filename order by the runner in `db/migrations.py`. Re-running is a no-op due to the `applied_migrations` ledger.

### `db/migrations/0001_init.sql`

```sql
-- HOA Chatbot v1 initial schema.
-- All timestamps stored as ISO 8601 UTC TEXT.
-- All FKs use ON DELETE CASCADE where appropriate.
-- WAL mode set on every connection by db/connection.py.

PRAGMA foreign_keys = ON;

-- ===== Migration ledger =====
CREATE TABLE IF NOT EXISTS applied_migrations (
  filename TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ===== Whitelisted addresses =====
CREATE TABLE IF NOT EXISTS addresses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  normalized_address TEXT NOT NULL UNIQUE,
  raw_address TEXT,
  source TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_addresses_normalized ON addresses(normalized_address);

-- ===== Documents =====
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  source TEXT NOT NULL,
  file_path TEXT,
  full_text TEXT,
  file_hash TEXT,
  gmail_message_id TEXT UNIQUE,
  is_active INTEGER NOT NULL DEFAULT 1,
  needs_ocr INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_documents_active ON documents(is_active);
CREATE INDEX IF NOT EXISTS idx_documents_gmail_msg ON documents(gmail_message_id);

-- ===== Chunks =====
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding BLOB NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

-- ===== Conversations =====
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  normalized_address TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_conversations_address ON conversations(normalized_address);

-- ===== Messages =====
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

-- ===== Chat rate log =====
CREATE TABLE IF NOT EXISTS chat_rate_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  normalized_address TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_rate_addr_time ON chat_rate_log(normalized_address, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_rate_created ON chat_rate_log(created_at);

-- ===== Failed address attempts =====
CREATE TABLE IF NOT EXISTS failed_address_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_address TEXT NOT NULL,
  normalized_address TEXT NOT NULL,
  ip_hash TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_failed_addr_norm ON failed_address_attempts(normalized_address);

-- ===== Admin login attempts =====
CREATE TABLE IF NOT EXISTS admin_login_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip_hash TEXT NOT NULL,
  succeeded INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_admin_attempts_ip_time ON admin_login_attempts(ip_hash, created_at);

-- ===== Config (key-value, with optional Fernet ciphertext) =====
CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value TEXT,
  value_encrypted TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Order of creation

The order in the file already respects dependencies: `addresses` is independent; `documents` precedes `chunks`; `conversations` precedes `messages`; `chat_rate_log`, `failed_address_attempts`, `admin_login_attempts`, `config` are all independent.

### Seed inserts (run by `scripts/init_db.py` after migration apply)

- `INSERT OR IGNORE INTO config (key, value) VALUES ('hoa_contact_email', 'hoa@example.com')` (admin overwrites in `/admin/config`).
- No password seeded; the script prints a reminder to run `set_admin_password.py`.

---

## 7. Phase-by-phase build order

The eight phases from brief section 10. Each phase ends with a manual verification step (no automated test suite per global rules). Estimates are agent compute time per the global instructions.

### Phase 1: Skeleton and DB

Deliverables:
- `requirements.txt`, `.env.example`, `.gitignore`, `Procfile`, `README.md` skeleton, `Dockerfile`, `scripts/start.py`.
- `db/connection.py`, `db/migrations.py`, `db/migrations/0001_init.sql`, `db/migrations/README.md`.
- All indexes from section 6 SQL: `idx_addresses_normalized`, `idx_documents_active`, `idx_documents_gmail_msg`, `idx_chunks_doc`, `idx_conversations_session`, `idx_conversations_address`, `idx_messages_conv`, `idx_messages_created`, `idx_chat_rate_addr_time`, `idx_chat_rate_created` (the retention-sweep companion index added per item 7 of the binding scope), `idx_failed_addr_norm`, `idx_admin_attempts_ip_time`.
- `scripts/init_db.py`, `scripts/set_admin_password.py`.
- `app.py` factory + blueprint registration stubs + error handlers.
- `routes/__init__.py`, `routes/public.py` stub (one route returning "ok"), `routes/admin.py` stub.
- `services/__init__.py`, `services/crypto.py`, `services/config_store.py`, `services/admin_auth.py`.
- `git init`, first commit on `feat/hoa-chatbot-v1` (docs-only commit per section 12 commit ordering).

Files touched: ~15 files.

Manual verification:
- `python scripts/init_db.py` runs and produces a populated SQLite file with all tables.
- `python scripts/set_admin_password.py` accepts a password and writes a row.
- `flask run` (with `app:create_app()` factory) boots without error and `/` returns 200.

Estimated compute time: 15 to 25 min. Simple (boilerplate + schema).

### Phase 2: Address gate and chat UI shell

Deliverables:
- `services/address_normalize.py`, `services/address_store.py`.
- `services/mailto.py` (used by failed page and chat error fallback).
- `services/rate_limit.py` (admin login portion built but not yet wired).
- Public routes: `/`, `/gate`, `/gate/failed`, `/chat`.
- Templates: `base.html`, `address_gate.html`, `address_failed.html`, `chat.html`.
- `static/style.css` (initial), `static/chat.js` (skeleton; full POST behavior in phase 3).
- Failed-attempt logging.
- Session continuity: 30-day permanent session on gate hit.

Files touched: ~12 files.

Manual verification:
- Submit a known-good address from `/`: cookie set, redirect to `/chat`.
- Submit a typo: `/gate/failed` rendered with mailto link, row written to `failed_address_attempts`.
- Reload `/chat` after gate hit: still gated (cookie persists).

Estimated compute time: 20 to 30 min. Simple to moderate.

### Phase 3: Embeddings + retrieval + chat completion

Deliverables:
- `services/embeddings.py` (Voyage client + `validate_existing_chunks`).
- `services/retrieval.py` (cosine top-k).
- `services/chat.py` (rate-limit, retrieval, Anthropic, fallbacks).
- `/api/chat` POST endpoint in `routes/public.py`.
- `messages` and `conversations` writes on every chat.
- Confirm Anthropic + Voyage model strings (brief open decisions 1 and 2).
- Confirm `EMBEDDING_DIM` constant.

Files touched: ~6 files.

Manual verification:
- Seed one document via Python REPL or temporary admin route. Send a chat. Confirm a relevant response comes back with the document title cited.
- Send 51 chats from one address; confirm rate-limit message on attempt 51.
- Temporarily empty `chunks`; confirm "still being set up" fallback.

Estimated compute time: 30 to 45 min. Moderate (model integration + math).

### Phase 4: Document admin (upload + chunk + embed)

Deliverables:
- `services/documents.py`.
- `routes/admin.py`: `/admin/login`, `/admin/dashboard`, `/admin/documents` (list, upload, paste, toggle, reembed, soft-delete).
- Templates: `admin/login.html`, `admin/dashboard.html`, `admin/documents.html`.
- Admin login rate limit wired.
- CSRF tokens on all admin POSTs.

Files touched: ~8 files.

Manual verification:
- Log in with the password set in phase 1.
- Upload a small PDF; confirm chunk count appears on dashboard.
- Send a chat about the PDF's content; confirm citation.
- Try 6 wrong logins from one IP in 15 min; confirm 429.
- Upload a PDF >100KB that pypdf cannot extract from; confirm "needs OCR" badge.

Estimated compute time: 35 to 50 min. Moderate.

### Phase 5: Admin conversations + failed addresses + config + addresses

Deliverables:
- `/admin/conversations` (list + view).
- `/admin/failed-addresses` with one-click whitelist promotion.
- `/admin/addresses` (new page added per plan-stage decision 1).
- `/admin/config` (HOA email, change password, Gmail status).
- Templates for each.

Files touched: ~6 files.

Manual verification:
- Click through conversation history; transcript renders.
- Promote a failed address; confirm subsequent gate submit succeeds.
- Change admin password via UI; log out and log back in with new password.
- Update HOA contact email; confirm mailto links use the new address.

Estimated compute time: 25 to 40 min. Simple to moderate.

### Phase 6: Gmail ingest

Deliverables:
- `scripts/gmail_oauth_setup.py`.
- `services/gmail_ingest.py` (run_once, dedup by `gmail_message_id` and `file_hash`, applies `hoa-bot-processed` label).
- `data/whitelisted_senders.example.txt`.
- "Test ingest" button on `/admin/config` calls `gmail_ingest.run_once`.

Files touched: ~4 files.

Manual verification:
- Bootstrap OAuth via `python scripts/gmail_oauth_setup.py` against a test Gmail.
- Send a test email from a whitelisted sender; click "test ingest" in admin; confirm new document and `hoa-bot-processed` label on the email.
- Click again; confirm dedup (no duplicate document).
- Send an email from a non-whitelisted sender; confirm not ingested.

Estimated compute time: 40 to 55 min. Moderate to complex (OAuth flow integration, Gmail API).

### Phase 7: APScheduler + retention

Deliverables:
- `scheduler.py` with both jobs registered.
- App-factory call to `start_scheduler(app)` (skipped via `FLASK_SKIP_SCHEDULER` for scripts).
- Retention sweep deletes `messages` >90d, orphan `conversations`, `chat_rate_log` >48h.

Files touched: ~3 files.

Manual verification:
- Temporarily set Gmail job to 1-min interval; confirm it fires and logs a count.
- Insert a fake `messages` row dated 100 days ago, run retention manually; confirm it is deleted.
- Revert intervals.

Estimated compute time: 15 to 25 min. Simple.

### Phase 8: Deploy

Deliverables:
- Railway project created; volume mount `/data` 1GB; env vars set per `.env.example`; `ENCRYPTION_KEY` generated and set.
- GitHub remote configured via SSH (`git@github.com:ToddWelch/hoa-chatbot.git`).
- First push triggers Railway build.
- `railway run python scripts/init_db.py` (idempotent; runs migrations).
- `railway run python scripts/set_admin_password.py` (interactive).
- `railway run python scripts/gmail_oauth_setup.py` (interactive OAuth).
- Smoke test end to end against the Railway-provided subdomain.

Files touched: 0 code files; ops only.

Manual verification:
- Public URL responds.
- Admin login works.
- Address gate works.
- Chat returns a real Anthropic response with citation.
- Mailto link opens correctly with subject + body URL-encoded.
- Send a test email from a whitelisted sender; wait 15 min; confirm ingest.
- Trigger a 51st chat; confirm rate-limit copy.

Estimated compute time: 25 to 40 min (mostly waiting on Railway). Simple to moderate (depending on Railway quirks).

### Total

Lower bound: 15+20+30+35+25+40+15+25 = 205 min = 3.4 hours agent compute.
Upper bound: 25+30+45+50+40+55+25+40 = 310 min = 5.2 hours agent compute.

Mid estimate to relay: ~4 hours agent compute, single-shot build.

---

## 8. External dependencies

Pinned versions for `requirements.txt`. All flagged with `COST FLAG:` where applicable.

```
Flask==3.1.0
gunicorn==23.0.0
APScheduler==3.10.4
pypdf==5.1.0
bcrypt==4.2.1
cryptography==44.0.0
python-dotenv==1.0.1
httpx==0.28.1
anthropic==0.39.0
voyageai==0.3.2
google-api-python-client==2.151.0
google-auth==2.36.0
google-auth-oauthlib==1.2.1
numpy==2.1.3
```

Notes:
- `Flask` 3.1.0: matches WCC pinning. Ships with Werkzeug + Jinja2 + itsdangerous.
- `gunicorn` 23.0.0: matches WCC. WSGI server.
- `APScheduler` 3.10.4: matches WCC. In-process scheduler.
- `pypdf` 5.1.0: text extraction. Free.
- `bcrypt` 4.2.1: password hashing. Direct dep (not via Flask-Bcrypt) to keep extension count down.
- `cryptography` 44.0.0: matches WCC. Provides Fernet.
- `python-dotenv` 1.0.1: matches WCC. `.env` loader.
- `httpx` 0.28.1: matches WCC. Used by Voyage client (and fallback for any HTTP needs Anthropic SDK doesn't cover).
- `anthropic` 0.39.0: official SDK. **`COST FLAG: Anthropic Haiku, ~$0.001 per chat.`**
- `voyageai` 0.3.2: official SDK. **`COST FLAG: Voyage embeddings, free tier covers v1, monitor as scale grows.`**
- `google-api-python-client` 2.151.0: Gmail API. Free.
- `google-auth` 2.36.0 + `google-auth-oauthlib` 1.2.1: OAuth flow. Free.
- `numpy` 2.1.3: cosine math + BLOB serialization. Free.

`COST FLAG: Railway compute Hobby ~$5/mo, Railway volume 1GB ~$0.25/mo.` (Infrastructure, not pip deps; reiterated here for visibility.)

No paid dep added without a flag.

---

## 9. Manual verification checklist (final)

What Todd does at the end of phase 8 to confirm the build works. One bullet each.

- [ ] Railway-provided subdomain resolves and serves the address-gate page over HTTPS.
- [ ] Admin login at `/admin/login` accepts the password set via `scripts/set_admin_password.py`; wrong password returns "Invalid credentials"; six wrong tries from one IP returns a 429.
- [ ] Submitting a whitelisted address at `/` redirects to `/chat`; the chat-gate cookie persists across reload.
- [ ] Submitting a non-whitelisted address renders `address_failed.html` with the mailto link; a row appears in `/admin/failed-addresses`.
- [ ] Chat at `/chat` returns a grounded Anthropic response that names a document title from a previously uploaded PDF.
- [ ] Mailto fallback link, when clicked, opens the user's mail client to a draft addressed to the configured HOA email with subject and body URL-encoded.
- [ ] Sending a test email from a whitelisted sender to the connected Gmail account, then waiting up to 15 minutes (or clicking "test ingest" in admin), produces a new row in `/admin/documents` and applies the `hoa-bot-processed` label on the email.
- [ ] Rapidly sending 51 chats from one whitelisted address triggers the daily rate-limit copy on attempt 51; the rate resets after 24 hours.
- [ ] Uploading a small text-extractable PDF in `/admin/documents` produces a new row in the documents list with `chunks_count > 0` (visible in the dashboard or list view) and the file is saved under `${DATA_DIR}/documents/`.
- [ ] Soft-deleting an uploaded document in `/admin/documents` causes subsequent chat answers to no longer cite that document title; the row is hidden from the active document list but remains in the DB (`is_active=0`).
- [ ] With zero active chunks (delete all rows from `chunks` via a quick SQL command, or disable all documents), submitting a chat returns the friendly "still being set up" copy with the mailto link, NOT a 500 error.
- [ ] With a deliberately invalid `ANTHROPIC_API_KEY` (or `VOYAGE_API_KEY`), submitting a chat returns the friendly "service unavailable" copy with the mailto link, NOT a 500 error; the underlying error is logged for the admin.

---

## 10. Risks and unknowns

Specific things that could break, each with a mitigation.

- **Railway volume mount semantics**: the volume is only mounted on the `web` service; `railway run` is supposed to inherit the same env and mount, but historically there have been edge cases where one-shot processes do not see the mount or see a stale snapshot.
  - Mitigation: README documents that all `scripts/*.py` invocations in prod must use `railway run` (not `railway shell` or detached jobs). `scripts/init_db.py` checks that `os.path.exists(os.path.dirname(DATABASE_PATH))` returns True and prints a clear error if not.

- **Voyage rate limits / free-tier policy change**: the free tier could be shut off or rate-limited differently mid-build.
  - Mitigation: `services/embeddings.py` raises `EmbeddingsUnavailable` on 429; `services/chat.py` catches and renders the friendly fallback. No 500 leaks. If the free tier is gone, swap the model id or provider in one place.

- **Gmail OAuth quirks**: `gmail.modify` scope sometimes requires app verification for production use; for a personal-account test deployment Google may show a "this app isn't verified" warning that the operator must dismiss.
  - Mitigation: `scripts/gmail_oauth_setup.py` prints a one-line note pointing the operator to the "Advanced -> Continue" link. The HOA's Gmail account is single-user; verification is not required for v1.

- **Embedding dimension change after data exists**: if Voyage releases a new default model that returns a different dim, the boot-time validator refuses to start.
  - Mitigation: README "Operations" section documents the procedure: bump `EMBEDDING_DIM` constant, run a one-shot script to delete all rows from `chunks`, restart, click "reembed" on each document. Same procedure if the operator wants to switch providers.

- **APScheduler missed-runs after a Railway deploy restart**: every deploy restarts the worker; jobs scheduled at exact minutes can silently miss a tick.
  - Mitigation: gmail ingest interval is 15 min, retention is daily. Both have catch-up runs at boot via `scheduler.start()` running missed jobs once. Acceptable for v1.

- **SQLite "database is locked" under load**: the chat write path (`messages` insert + `chat_rate_log` insert) is on the same connection as scheduler writes; if the gmail ingest runs at the moment a chat insert is happening, we get a lock contention.
  - Mitigation: WAL mode + 3-retry backoff on every write per `db/connection.py`. v1 traffic is tiny (single-digit concurrent chats); contention is unlikely. If observed in logs, raise to Crash Override at v2.

- **Bcrypt cost factor**: default cost factor of 12 is fine; if an operator picks something absurd via env, login latency grows.
  - Mitigation: hardcode `bcrypt.gensalt(12)` in `services/admin_auth.py`. No env override.

- **CSRF on admin POSTs without Flask-WTF**: hand-rolled tokens have a small risk of implementation error.
  - Mitigation: ~30 LOC implementation, single token generator + comparator, used uniformly via decorator. CO is asked to spot-check the implementation in the build review.

- **APScheduler with `--threads 4`**: APScheduler runs in its own threads, separate from the gunicorn request threads; no interaction expected.
  - Mitigation: noted explicitly in `scheduler.py` docstring; if any race is observed, switch to `--threads 1` and accept slightly worse concurrent-chat latency.

- **Gmail attachment with no filename**: `_extract_message` could trip on unusual MIME structure.
  - Mitigation: catch per-message exceptions, log + skip, continue with the next message; do not let one bad email kill the whole run.

---

## 11. Estimated complexity per phase

| Phase | Complexity | Reasoning |
|---|---|---|
| 1 | Simple | Boilerplate + schema. Mostly typing. |
| 2 | Simple to moderate | Address normalization is a careful regex; failed-attempt logging is one insert. |
| 3 | Moderate | Two external service integrations (Voyage, Anthropic) with error paths and a fallback shape. |
| 4 | Moderate | PDF parsing edge cases (needs_ocr, large files), chunking + embedding chain, admin auth + CSRF + rate limit. |
| 5 | Simple to moderate | Mostly admin UI; the "promote failed address to whitelist" action is the only non-trivial bit. |
| 6 | Moderate to complex | Gmail OAuth setup script + ingest pipeline + label management. Most failure-prone integration in the build. |
| 7 | Simple | Two job wrappers, an interval setup, a daily setup. |
| 8 | Simple to moderate | Mostly Railway clicks + smoke tests; complexity if Railway has a snag. |

Total: agent compute time ~3.4 to 5.2 hours, mid ~4 hours, single-shot build on `feat/hoa-chatbot-v1`.

---

## 12. Branch and commit plan

- Single branch: `feat/hoa-chatbot-v1`.
- Phase 1 commit ordering: the FIRST commit on `feat/hoa-chatbot-v1` is docs-only and contains exactly `BUILD_BRIEF.md`, `IMPLEMENTATION_PLAN.md`, the `README.md` skeleton, and `.gitignore`. Commit message: `docs: brief, plan, and gitignore for HOA chatbot v1`. No code is included in this first commit so the planning documents land cleanly and any later diff-of-code does not drown in the docs.
- All subsequent commits add code in phase order (phase 1 code commit, then phases 2 through 8 each as their own commit), with descriptive messages.
- After phase 8 verification by Todd, merge to `main` via fast-forward (no PR review required for greenfield single-author branch unless Todd explicitly asks). CO does the post-build review on the merged branch.

---

## 13. Open questions for Todd at build time (not blockers)

- **Whitelisted addresses source**: paste a list at first deploy, or import from a CSV? The plan supports both via `scripts/init_db.py --seed-addresses <path>`. (Brief open decision 4.)
- **HOA contact email**: confirm at first deploy via `/admin/config` or seed in `init_db.py`. Default placeholder is `hoa@example.com`. (Brief open decision 5.)
- **Whether to keep Gmail OAuth client id/secret in env vs DB**: plan keeps client id/secret in env for bootstrap simplicity per brief default; refresh token always in DB encrypted. (Brief open decision 6.)
- **Domain**: Railway-provided subdomain for v1; custom domain later if Todd wants.

These are confirmed at the start of phase 8, not blockers for the plan.

---

## 14. Trinity handshake binding scope (extracted for future build dispatch)

When the orchestrator dispatches the build, it should extract these as the binding scope and Trinity will echo them back in the handshake before any code is written:

1. Create greenfield repo on branch `feat/hoa-chatbot-v1`, all files listed in section 5 above.
2. SQLite schema per section 6 (file `db/migrations/0001_init.sql`).
3. Whitelist storage in DB `addresses` table, not flat file (plan-stage decision 1).
4. Gmail refresh token at `config.value_encrypted` for `key='gmail_refresh_token'`, with constant `KEY_GMAIL_REFRESH_TOKEN` in `services/config_store.py` (plan-stage decision 2).
5. Retrieval split out of chat: `services/retrieval.py` and `services/chat.py` (plan-stage decision 3).
6. `chat_rate_log` retention sweep at 48 hours; `messages` retention at 90 days (plan-stage decision 4 + brief).
7. `EMBEDDING_DIM` constant validated against existing chunk BLOB lengths at app boot (plan-stage decision 6).
8. Gmail OAuth bootstrap is `railway run python scripts/gmail_oauth_setup.py` (plan-stage decision 7).
9. `app.permanent_session_lifetime = timedelta(days=30)`, `session.permanent = True` on chat-gate hit; admin uses idle-timeout pattern (plan-stage decision 9).
10. `scripts/set_admin_password.py` and `/admin/config/password` share `services.admin_auth.hash_password` and write the same `config` row (plan-stage decision 10).
11. Procfile + Dockerfile + `scripts/start.py` per section 5 (Root subsection).
12. `requirements.txt` pinned per section 8.
13. Manual verification checklist per section 9 must pass before declaring complete.
14. Address normalization per brief lines 86 to 92: lowercase, trim, collapse whitespace, strip apt/unit suffix via the apt/unit regex, expand the abbreviation map (st, rd, dr, ln, ct, cir, ave, blvd, pl). The token "way" stays as "way" and is not abbreviated. Implemented in `services/address_normalize.py`.
15. Mailto safety per brief lines 99 to 103: URL-encode all values via `urllib.parse.quote(safe='')`, strip every CR and LF from the input strings before encoding, cap the subject line at 78 characters, cap the body at 1800 characters, append the truncation suffix when the body is cut. Implemented in `services/mailto.py`.
16. Gmail ingest: APScheduler interval is 15 minutes; query filter is `is:unread -label:hoa-bot-processed from:(<whitelisted_senders>)` per brief; dedup uses `documents.gmail_message_id` UNIQUE constraint AND attachment `file_hash`; on success, apply the `hoa-bot-processed` label and do NOT mark the email as read. Implemented in `services/gmail_ingest.py` and `scheduler.py`.
17. CSRF: hand-rolled session-bound tokens via `services/csrf.py` (`secrets.token_urlsafe(32)`, stored in `session["csrf_token"]`, persisted across POSTs, exposed via the `inject_csrf_token` Jinja context processor). The `@require_csrf` decorator validates with `hmac.compare_digest` (never `==`) against `request.form.get("csrf_token")` or `request.headers.get("X-CSRF-Token")`. On failure, `abort(403)` renders `templates/errors/csrf_403.html`. Scope: every admin POST, the public `/api/chat` POST (header-based, read from `<meta name="csrf-token">`), and the address-gate POST (form field).
18. Admin rate limit: 5 attempts per IP per 15 minutes, every attempt logged to `admin_login_attempts` (with `succeeded` flag), exceeded-limit response is a friendly cooldown message (not a generic 429). Implemented in `services/rate_limit.py` and `routes/admin.py`.
19. `DATA_DIR` env var: default `./data`, set to `/data` on Railway. Used as the base path for `whitelisted_senders.txt` and seed-only files. The `addresses` table is DB-backed and unaffected.

Out of scope: anything in brief section 13 (multi-tenant, React, Postgres, automated tests, OCR, streaming, Pub/Sub, doc versioning, per-resident accounts, translation, mobile wrapper, analytics dashboard beyond counts, 2FA, automated backups).

---

End of plan.
