# HOA Chatbot, Build Brief (v1)

Status: draft for build kickoff. Operator priority order is functional, usable, cheapest. This brief is the binding contract for the build phase.

---

## 1. Latitude decisions

The orchestrator granted wide latitude to restructure file layout, evaluate Postgres vs SQLite, evaluate alternative embedding providers (with `COST FLAG:`), drop or simplify non-load-bearing v1 features, and add clearly-wanted features the original brief omitted. Items exercised below.

1. **SQLite retained over Postgres.** Postgres on Railway adds about $5/mo and removes no v1 pain (single-writer load, low row counts, file-shape data). SQLite + WAL + Railway volume is functional, usable, and cheapest. Postgres revisit deferred to v2 if concurrent write contention shows up in logs.
2. **Voyage retained as embedding provider.** Free tier (200M tokens, expected v1 usage well under 1M tokens) is the cheapest viable option with quality comparable to OpenAI text-embedding-3-small. No alternative evaluated worth switching for at v1 scale. `COST FLAG:` retained for re-eval at scale.
3. **Anthropic Haiku retained for chat completion.** Sonnet is roughly 12x more expensive per token; Haiku quality is sufficient for HOA Q&A grounded in retrieved chunks. Sonnet revisit allowed if grounding quality complaints surface in admin review.
4. **`app.py` split into 4 files at v1, not deferred.** The original brief implied a single `app.py`. The 300 LOC + two-responsibility rule means the split happens at v1 anyway. Doing it on day one is cheaper than refactoring later.
5. **Added `admin_login_attempts` table for rate limiting.** Original brief did not include rate limiting on admin login. Brute force on a single password is the obvious threat. Adding it now is trivial; retrofitting is not.
6. **Added per-address chat rate limit (50/24h).** Cost protection. A single bad actor (or runaway script) could otherwise rack up Anthropic charges on one address. 50 chats per address per day is generous for legitimate use and a hard cap on abuse.
7. **Replaced "near-zero monthly cost" with realistic estimate.** Honesty beats wishful thinking. Real number is $5 to $10/mo. See cost table.
8. **Failed-address logging surfaced to admin dashboard.** Helps tune the whitelist (typos, unit number variants) instead of leaving residents stuck at the gate.
9. **Empty-chunks and external-service failure UX paths added.** Originally implied as 500 errors. v1 should never 500 in front of a resident; explicit fallback copy is small effort, large UX win.
10. **Gmail OAuth refresh token moved from env var to encrypted DB row.** Refresh tokens rotate; restarting Railway to swap an env var is bad ops hygiene. Storing in `config` table encrypted via Fernet is the standard pattern and matches the global Secrets-and-Encryption rule.

---

## 2. Project Identity

**Name:** HOA Chatbot

**One-liner:** A small Flask web app that answers homeowner questions about a single HOA's governing documents, gated by address whitelist, with an admin panel for document management and a Gmail ingestion pipeline for HOA correspondence.

**Audience:** Homeowners in one specific HOA. Single-tenant, single-board admin. Not a SaaS product; not multi-HOA. v2 may add multi-tenancy.

**Deployment target:** Railway, single service, single gunicorn worker.

---

## 3. Threat model

The address gate is a speed bump, not a security boundary. Its job is to discourage casual scraping and keep the chatbot off the public internet's drive-by radar; it is not designed to defeat a determined attacker who already has a resident's address (publicly available data). The admin panel is a separate trust boundary, protected by bcrypt password, distinct session flag, rate limiting, CSRF, and short idle expiry; it must withstand brute force and session-fixation. HOA documents are not high-secrecy: CC&Rs, bylaws, meeting minutes are typically posted on county recorder sites or distributed broadly to residents. The system is not a place to store sensitive personal data; do not log resident PII beyond what is required to operate the address gate (normalized address, timestamp, hashed IP). The expected attacker profile is a curious neighbor, an opportunistic scraper, or a frustrated former resident, not a state-level adversary.

---

## 4. Stack

| Layer | Choice | Notes |
|---|---|---|
| Runtime | Python 3.11 | Railway default works. |
| Web framework | Flask 3.x | Blueprints for routes/public.py and routes/admin.py. |
| WSGI server | gunicorn | Pinned to 1 worker (APScheduler in-process). |
| DB | SQLite + WAL mode | On Railway volume mount. |
| Migrations | Hand-written `.sql` files in `migrations/` | Run via init script on deploy. |
| Scheduler | APScheduler (BackgroundScheduler) | In-process; required worker pin. |
| Vector search | Voyage embeddings + numpy cosine | `COST FLAG:` Voyage. Stored as BLOB in SQLite. |
| Chat completion | Anthropic Haiku | `COST FLAG:` Anthropic. |
| Encryption | `cryptography.fernet` | `ENCRYPTION_KEY` env var. |
| Auth (admin) | bcrypt password + Flask session cookie | Distinct session flag from chat gate. |
| Email ingest | Gmail API with OAuth refresh token | Token stored encrypted in DB. Scope `gmail.modify`. |
| Frontend | Server-rendered Jinja templates + minimal vanilla JS for chat | No React in v1. Cheaper, faster, lower attack surface. |
| PDF parsing | pypdf | `COST FLAG:` none (free). OCR not in v1. |

`COST FLAG:` Anthropic Haiku, Voyage embeddings, Railway compute, Railway volume. See cost table.

---

## 5. Cost estimate

`COST FLAG:` Realistic monthly costs, replacing earlier "near-zero" claim. Numbers assume 500 chats/month, 30 documents (about 3000 chunks), and ~10 inbound HOA emails/week.

| Line item | Estimate | Notes |
|---|---|---|
| Railway compute (Hobby plan, 1 service) | $5.00/mo | $5 baseline included; this app fits. |
| Railway volume (1 GB) | $0.25/mo | $0.25/GB-mo. |
| Anthropic Haiku, 500 chats/mo | $0.50 to $2.00/mo | ~$0.001 per chat at typical context size. Confirm at build time. |
| Voyage embeddings | $0.00/mo | Free tier covers v1 ingest + chats easily. |
| Gmail API | $0.00/mo | Free for our volume. |
| Domain (optional) | $0 to $1/mo | Use Railway-provided subdomain for v1. |
| **Total expected** | **$5 to $10/mo** | Headroom for spikes built in. |

Watchpoints: Anthropic spend scales with chat volume and context size. If chats jump above ~2000/mo, revisit caching for retrieved chunks. Voyage free-tier ceiling is 200M tokens; v1 will not approach this.

---

## 6. Core Features (v1 scope)

1. **Address gate (chat-side speed bump).**
   - Public landing page asks for the resident's street address.
   - Submitted address is normalized (lowercase, strip leading/trailing whitespace, strip apt/unit suffixes via regex `(apt|unit|#)\s*\w+\s*$`, expand abbreviations: `st`->`street`, `rd`->`road`, `dr`->`drive`, `ln`->`lane`, `ct`->`court`, `cir`->`circle`, `ave`->`avenue`, `blvd`->`boulevard`, `pl`->`place`; `way` stays as `way`).
   - Normalized address compared against `data/valid_addresses.txt` (one per line, normalized at load).
   - On hit: set chat session flag, 30-day cookie (HttpOnly, Secure, SameSite=Lax), redirect to chat.
   - On miss: friendly error page with "email the HOA" mailto link; the failed-attempt address is logged to a `failed_address_attempts` table for admin review (whitelist tuning).
2. **Chat UI.**
   - Single-page chat at `/chat` (gated).
   - Last 6 message pairs stored in Flask session cookie (4KB-safe).
   - Full conversation written to `conversations` and `messages` tables (DB-backed history; cookie is just a hot cache).
   - Submit button calls `/api/chat` (POST JSON), response renders in the chat window.
   - Empty-chunks fallback: if `chunks` table is empty, return "the chatbot is still being set up, please email the HOA in the meantime" with the mailto link, not a 500.
   - External-service failure fallback: try/except around Voyage and Anthropic calls; on failure, return "AI service is having trouble, please try again or email the HOA" with the mailto link, not a 500.
   - Per-address rate limit: 50 chats per normalized address per rolling 24 hours. On hit, return "you've reached today's chat limit, please email the HOA for further questions" with the mailto link.
3. **Mailto fallback link.**
   - Subject and body are URL-encoded with `urllib.parse.quote(safe='')`.
   - CR/LF bytes stripped before encoding (header injection defense).
   - Subject capped at 78 characters.
   - Body capped at 1800 characters; if truncated, append "(message truncated, please paste from chat)".
   - Recipient set from `config` table value `hoa_contact_email`.
4. **Admin panel (separate trust boundary).**
   - `/admin/login`: bcrypt password check against `config.admin_password_hash`. Distinct session flag (`admin_authenticated`) from chat gate.
   - Failed login rate limit: 5 attempts per IP per 15 minutes, logged to `admin_login_attempts`. On hit, return generic 429.
   - Admin session cookie: Secure, HttpOnly, SameSite=Strict, 1-hour idle expiry. Chat cookie remains 30 days.
   - CSRF tokens on every admin POST route (Flask-WTF or hand-rolled session-bound tokens).
   - `/admin/dashboard`: counts of documents, chunks, chats today/30d, failed-address attempts, OCR-needed PDFs, Gmail ingest status.
   - `/admin/documents`: upload PDF or paste text, edit metadata, mark `is_active=0/1`, delete (soft delete via `is_active`), trigger re-embed.
   - `/admin/conversations`: list recent conversations, view full transcript, filter by address.
   - `/admin/failed-addresses`: list failed gate attempts with one-click "add to whitelist" action.
   - `/admin/config`: edit HOA contact email, admin password (current+new), Gmail-related settings.
5. **Document ingestion pipeline.**
   - PDF: pypdf extracts text. If a PDF >100KB extracts <50 chars, mark `is_active=0` and surface as "needs OCR" in admin (no OCR pipeline in v1; admin can paste text manually).
   - Text: chunk at ~800 tokens with ~100 token overlap, embed via Voyage, store in `chunks` table.
   - Per-document: store original file in volume at `data/documents/<doc_id>.pdf`, store extracted text in `documents.full_text`.
6. **Vector retrieval.**
   - On chat: embed the user query via Voyage, cosine-similarity scan over active chunks, take top K=6.
   - Pass top chunks plus last 6 message pairs to Anthropic Haiku as context.
   - System prompt instructs the model to ground answers in provided context, cite document titles, and decline gracefully when the answer is not in the documents.
7. **Gmail ingestion (HOA correspondence).**
   - APScheduler job runs every 15 minutes.
   - Filter: `is:unread -label:hoa-bot-processed from:<whitelisted_senders>` where whitelisted senders are loaded from `data/whitelisted_senders.txt`.
   - For each matched message: extract subject + body text + attachments, run dedup against `documents.gmail_message_id`, ingest as a new document on first sight.
   - Attachment dedup uses `file_hash` (SHA-256 of file bytes).
   - On successful ingest: apply `hoa-bot-processed` label (do NOT mark-as-read; preserves the user's inbox state).
   - Required OAuth scope: `https://www.googleapis.com/auth/gmail.modify`.
   - Refresh token stored encrypted in `config.gmail_refresh_token_encrypted` via Fernet; bootstrap via `scripts/gmail_oauth_setup.py`.
8. **Conversation retention.**
   - APScheduler daily job deletes `messages` rows older than 90 days. `conversations` rows with no remaining messages also deleted.
   - `chat_log` (legacy if used) follows same 90-day rule.
9. **SQLite reliability.**
   - At DB init, set `PRAGMA journal_mode=WAL`.
   - Wrap writes in retry-with-backoff (3 retries: 100ms, 250ms, 500ms) on `sqlite3.OperationalError: database is locked`.
10. **Observability.**
    - Standard Flask logging to stdout (Railway captures it).
    - Admin dashboard surfaces user-facing operational info (counts, errors, OCR needs).

---

## 7. Database Schema

SQLite. All timestamps stored as `TEXT` in ISO 8601 UTC. All tables include `created_at TEXT NOT NULL DEFAULT (datetime('now'))`.

```sql
-- core: governing documents and ingested HOA correspondence
CREATE TABLE documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  source TEXT NOT NULL,                    -- 'admin_upload' | 'admin_paste' | 'gmail'
  file_path TEXT,                          -- relative path under data/documents/
  full_text TEXT,
  file_hash TEXT,                          -- SHA-256 of file bytes (for attachment dedup)
  gmail_message_id TEXT UNIQUE,            -- NULL for non-Gmail sources; UNIQUE for dedup
  is_active INTEGER NOT NULL DEFAULT 1,    -- 0 = soft deleted or needs OCR
  needs_ocr INTEGER NOT NULL DEFAULT 0,    -- 1 = pypdf extracted <50 chars on >100KB PDF
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_documents_active ON documents(is_active);
CREATE INDEX idx_documents_gmail_msg ON documents(gmail_message_id);

-- chunked text + embedding for retrieval
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding BLOB NOT NULL,                 -- numpy float32 array, serialized
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_chunks_doc ON chunks(document_id);

-- conversations: one per address per session, lifetime container
CREATE TABLE conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,                -- Flask session id (or hash thereof)
  normalized_address TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conversations_session ON conversations(session_id);
CREATE INDEX idx_conversations_address ON conversations(normalized_address);

-- messages: full chat history, DB-backed (cookie holds last 6 pairs only)
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                      -- 'user' | 'assistant'
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_messages_conv ON messages(conversation_id);
CREATE INDEX idx_messages_created ON messages(created_at);

-- per-address rate limit, 50/24h
CREATE TABLE chat_rate_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  normalized_address TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_chat_rate_addr_time ON chat_rate_log(normalized_address, created_at);

-- failed gate attempts (admin uses these to tune whitelist)
CREATE TABLE failed_address_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_address TEXT NOT NULL,
  normalized_address TEXT NOT NULL,
  ip_hash TEXT,                            -- SHA-256 of client IP, no raw IP storage
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_failed_addr_norm ON failed_address_attempts(normalized_address);

-- admin trust boundary: rate-limit failed logins
CREATE TABLE admin_login_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip_hash TEXT NOT NULL,                   -- SHA-256 of client IP
  succeeded INTEGER NOT NULL,              -- 0 = failed, 1 = succeeded
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_admin_attempts_ip_time ON admin_login_attempts(ip_hash, created_at);

-- key-value config: admin password hash, contact email, Gmail token, etc.
CREATE TABLE config (
  key TEXT PRIMARY KEY,
  value TEXT,                              -- plaintext for non-secret keys
  value_encrypted TEXT,                    -- Fernet-encrypted for secrets
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Seeded keys:
--   admin_password_hash         (value)
--   hoa_contact_email           (value)
--   gmail_refresh_token         (value_encrypted)
--   gmail_oauth_client_id       (value)
--   gmail_oauth_client_secret   (value_encrypted)
```

Notes:
- All foreign keys use `ON DELETE CASCADE` where the parent owns the child's lifecycle.
- IPs are stored hashed only. Raw IPs never persisted.
- Apply `PRAGMA journal_mode=WAL;` on every connection open during init.

---

## 8. File Structure

```
hoa-chatbot/
├── app.py                        # Flask factory, blueprint reg, error handlers, scheduler bootstrap call
├── scheduler.py                  # APScheduler init + job registration (gmail ingest, retention)
├── routes/
│   ├── __init__.py
│   ├── public.py                 # address gate + chat blueprint
│   └── admin.py                  # admin blueprint
├── services/
│   ├── __init__.py
│   ├── address_normalize.py      # abbreviation expansion + apt/unit strip
│   ├── embeddings.py             # Voyage client wrapper
│   ├── chat.py                   # retrieval + Anthropic call + fallbacks
│   ├── documents.py              # PDF parse, chunk, embed, store
│   ├── gmail_ingest.py           # Gmail API client + ingest loop
│   ├── crypto.py                 # Fernet encrypt/decrypt helpers
│   ├── rate_limit.py             # per-address chat limit + admin login limit
│   └── mailto.py                 # safe mailto URI construction
├── db/
│   ├── __init__.py
│   ├── connection.py             # sqlite3 connect + WAL + retry-on-locked
│   └── migrations/
│       ├── 001_init.sql
│       └── README.md
├── templates/
│   ├── base.html
│   ├── address_gate.html
│   ├── chat.html
│   ├── address_failed.html
│   └── admin/
│       ├── login.html
│       ├── dashboard.html
│       ├── documents.html
│       ├── conversations.html
│       ├── failed_addresses.html
│       └── config.html
├── static/
│   ├── chat.js                   # vanilla JS, no build step
│   └── style.css
├── scripts/
│   ├── init_db.py                # apply migrations, seed config keys
│   ├── gmail_oauth_setup.py      # bootstrap Gmail refresh token (encrypts and stores)
│   └── set_admin_password.py     # set/reset admin password
├── data/                         # gitignored, on Railway volume in prod
│   ├── documents/                # uploaded PDFs
│   ├── valid_addresses.txt       # gitignored
│   ├── valid_addresses.example.txt
│   ├── whitelisted_senders.txt   # gitignored
│   ├── whitelisted_senders.example.txt
│   └── app.db                    # SQLite file
├── .env                          # gitignored
├── .env.example
├── .gitignore
├── Procfile                      # gunicorn --workers 1 --timeout 120 --bind 0.0.0.0:$PORT app:app
├── requirements.txt
├── README.md
└── BUILD_BRIEF.md                # this file
```

Each Python module respects the 300 LOC + two-responsibility cap. Notable splits:
- `app.py` is factory + registration only; route logic lives in `routes/`.
- `routes/admin.py` should split into `routes/admin/<area>.py` (login, documents, conversations, config) if it crosses 300 LOC during build.
- `services/gmail_ingest.py` may split into `auth.py` + `fetch.py` + `ingest.py` if it crosses 300 LOC.

---

## 9. Environment Variables

`.env.example`:

```
# Flask
FLASK_SECRET_KEY=change_me_to_a_long_random_string
FLASK_ENV=production

# SQLite
DATABASE_PATH=/data/app.db                  # Railway volume mount path; for local dev use ./data/app.db

# Encryption (Fernet, 32-byte url-safe base64)
ENCRYPTION_KEY=change_me_use_python_-c_'from_cryptography.fernet_import_Fernet;print(Fernet.generate_key().decode())'

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-haiku-4-5            # confirm at build time per Open Decisions

# Voyage
VOYAGE_API_KEY=pa-...
VOYAGE_MODEL=voyage-3-lite                  # confirm at build time per Open Decisions

# Gmail OAuth (refresh token NOT here; stored encrypted in DB)
# Client id/secret may also be moved to DB; left here for bootstrap simplicity
GMAIL_OAUTH_CLIENT_ID=
GMAIL_OAUTH_CLIENT_SECRET=

# Operational
LOG_LEVEL=INFO
```

Explicitly NOT in `.env.example`:
- `GMAIL_REFRESH_TOKEN`: stored encrypted in `config.gmail_refresh_token` (Fernet, via `ENCRYPTION_KEY`). Bootstrap via `scripts/gmail_oauth_setup.py`.
- `ADMIN_PASSWORD`: stored as bcrypt hash in `config.admin_password_hash`. Set via `scripts/set_admin_password.py`.

`.gitignore` includes:
```
.env
*.db
*.db-journal
*.db-wal
*.db-shm
data/*.txt
!data/*.example.txt
data/documents/
__pycache__/
*.pyc
.venv/
```

---

## 10. Implementation Order

Build in this phase order. Each phase ends with manual verification (no automated test suite per global rules).

**Phase 1: Skeleton and DB.**
1. `requirements.txt`, `.env.example`, `.gitignore`, `Procfile`, `README.md` skeleton.
2. `db/connection.py` with WAL + retry-on-locked.
3. `db/migrations/001_init.sql` with all tables from section 7.
4. `scripts/init_db.py` to apply migrations + seed config keys.
5. `scripts/set_admin_password.py`.
6. `app.py` factory + blueprint registration stubs + error handlers (404, 500, 429).
7. `routes/public.py` and `routes/admin.py` blueprint stubs.
8. Verify: `python scripts/init_db.py` produces a populated SQLite file.

**Phase 2: Address gate + chat UI shell.**
1. `services/address_normalize.py`.
2. Public `/` (address gate form), `/gate` (POST), `/gate/failed`, `/chat` (gated GET).
3. `templates/address_gate.html`, `address_failed.html`, `chat.html`, `base.html`.
4. Failed-attempt logging to `failed_address_attempts`.
5. Verify manually: matching address sets cookie and redirects; mismatch logs and shows error page.

**Phase 3: Embeddings + retrieval + chat completion.**
1. `services/embeddings.py` (Voyage client; one function: `embed_texts(list[str]) -> list[np.ndarray]`).
2. `services/chat.py` with retrieval + Anthropic call + empty-chunks fallback + try/except service fallbacks + per-address rate limit check.
3. `services/rate_limit.py`.
4. `services/mailto.py`.
5. `/api/chat` POST endpoint.
6. Conversation + message DB writes.
7. Verify manually with a single seeded document.

**Phase 4: Document admin (upload + chunk + embed).**
1. `services/documents.py` (PDF parse, chunk, embed, store; mark needs_ocr).
2. `services/crypto.py` (Fernet helpers).
3. Admin login (`/admin/login`) with bcrypt + admin_login_attempts rate limit + CSRF.
4. `/admin/dashboard`, `/admin/documents` (upload, list, delete soft, re-embed).
5. Verify manually: upload PDF, chunk count appears, chat retrieves it.

**Phase 5: Admin conversations + failed addresses + config.**
1. `/admin/conversations` (list, view).
2. `/admin/failed-addresses` (list, "add to whitelist" action which appends to `data/valid_addresses.txt` and reloads in-memory set).
3. `/admin/config` (HOA contact email, admin password change, Gmail OAuth status).
4. Verify manually.

**Phase 6: Gmail ingest.**
1. `scripts/gmail_oauth_setup.py` (web flow, encrypts refresh token, writes to config).
2. `services/gmail_ingest.py` (fetch unread + label filter, dedup by gmail_message_id and file_hash, ingest via `documents.py`, apply hoa-bot-processed label).
3. Verify manually with a test Gmail account: send a test email, run ingest manually, confirm document and label.

**Phase 7: APScheduler + retention.**
1. `scheduler.py` with two jobs: gmail ingest (every 15 min), retention sweep (daily, deletes messages and chat_rate_log rows older than 90 days).
2. Hook scheduler init from `app.py` factory (single-worker assumption documented).
3. Verify manually that jobs run on schedule (use 1-min interval for first verification, then revert).

**Phase 8: Deploy.**
1. Create Railway project, add volume mount at `/data`, set env vars from `.env.example`.
2. Push to GitHub via SSH remote.
3. Confirm deploy, run `python scripts/init_db.py` via `railway run`.
4. Set admin password via `railway run python scripts/set_admin_password.py`.
5. Bootstrap Gmail OAuth via `railway run python scripts/gmail_oauth_setup.py` (or run locally pointing to prod DB; prefer local for ease of OAuth web flow, then copy DB or run on prod).
6. Smoke-test end to end.

---

## 11. Style and Convention Rules

- **No em dashes anywhere** (code, comments, templates, docs, error copy). Use commas, periods, semicolons, parentheses.
- **No automated test suite** in v1. Manual verification per phase.
- **Files under 300 LOC**; split when a file has two responsibilities.
- **API responses are JSON** for `/api/*` routes; HTML rendered for everything else.
- **RESTful path style**: `/api/<resource>/<action>` for the chat endpoint and any future API.
- **Snake_case** for variables, functions, DB columns; `PascalCase` for classes.
- **DB tables**: plural snake_case. Foreign keys: `<singular>_id`.
- **Timestamps**: ISO 8601 UTC text in DB; convert at the edge for display.
- **Secrets**: never logged, never returned in responses; `to_dict()`-style methods return `has_*` booleans, not values.
- **Error copy** is the user-facing fallback string; never expose stack traces to residents.
- **Logging**: stdout, INFO default, no PII beyond normalized address.

---

## 12. Operational notes

### Railway volume mount
- Service settings, add Volume: mount path `/data`, size 1 GB.
- `DATABASE_PATH=/data/app.db` in env.
- Uploaded documents live at `/data/documents/`.
- `data/valid_addresses.txt` and `data/whitelisted_senders.txt`: keep these in the repo path (`./data/...`) for editability via deploy, OR move to volume if admin will be the editor. Build-time decision: start in repo for simplicity, move to volume only if admin needs runtime edits beyond the failed-address one-click action.

### Manual backup procedure
- Weekly: `railway run sqlite3 /data/app.db ".backup /data/app.backup.db"`, then download with `railway run cat /data/app.backup.db > local-backup-$(date +%F).db`.
- Or: shut service briefly, snapshot the volume from Railway dashboard.
- Document this in README under "Operations." Backups not automated in v1.

### Gmail OAuth bootstrap
1. In Google Cloud Console: create OAuth client (Desktop app type), enable Gmail API.
2. Set `GMAIL_OAUTH_CLIENT_ID` and `GMAIL_OAUTH_CLIENT_SECRET` in `.env` (and on Railway).
3. Run `python scripts/gmail_oauth_setup.py` locally.
4. Script opens browser, user grants `gmail.modify` scope.
5. Script receives refresh token, encrypts with `ENCRYPTION_KEY`, writes to `config.gmail_refresh_token`.
6. For prod: either run script against prod DB (set `DATABASE_PATH` to prod path via tunnel), or run locally and `.dump`/`.import` the `config` row.
7. Verify by triggering a manual ingest run from admin.

### Deploy steps
1. Push to GitHub `main` branch via SSH remote.
2. Railway auto-deploys.
3. First deploy only: `railway run python scripts/init_db.py`, then `railway run python scripts/set_admin_password.py`, then Gmail OAuth bootstrap.
4. Subsequent deploys: migrations run via `init_db.py` (idempotent, only applies un-applied SQL files).

### Worker pinning
- `Procfile`: `web: gunicorn --workers 1 --timeout 120 --bind 0.0.0.0:$PORT app:app`
- Reason: APScheduler runs in-process; multiple workers would mean duplicate scheduled job runs.
- Tradeoff: single worker means concurrent requests serialize on Python GIL boundaries. v1 traffic is tiny; not a concern. Revisit at v2 if traffic grows; the fix is an external scheduler (Railway cron or separate worker service) and `--workers 2+`.

---

## 13. Out of Scope (v1)

- Multi-tenant (multiple HOAs).
- React or any SPA frontend.
- Postgres.
- Automated test suite.
- OCR pipeline for image-only PDFs (admin manually pastes text instead).
- Streaming chat responses (token-by-token); v1 returns the full response in one POST reply.
- Webhooks for Gmail (Pub/Sub push); v1 polls every 15 minutes.
- Document versioning (replacing a doc deletes the old chunks; no diff history).
- User accounts beyond the admin (no per-resident login).
- Translation / non-English support.
- Mobile-app wrapper.
- Analytics dashboard beyond simple counts.
- Two-factor admin auth.
- Automated backups (manual only in v1).

---

## 14. Open Decisions for Build Time

These need live verification at the start of Phase 3 (chat completion + embeddings):

1. **Anthropic model string.** Confirm exact id of the cheapest Haiku-class model currently available. Reference: https://docs.anthropic.com/en/docs/about-claude/models . Update `ANTHROPIC_MODEL` in `.env.example` and any constants accordingly.
2. **Voyage model string.** Confirm `voyage-3-lite` (or successor) is the right cost/quality choice. Reference: https://docs.voyageai.com/docs/pricing . Update `VOYAGE_MODEL` accordingly. Note the embedding dimension (changes the BLOB size in `chunks.embedding`).
3. **Per-chat token budget for Anthropic.** Once retrieval is wired up, measure typical context size (top 6 chunks + last 6 message pairs + system prompt) and confirm cost-per-chat estimate. If significantly above $0.002, reduce K or chunk size before going live.
4. **`data/valid_addresses.txt` source.** Decide: does Todd paste the address list in at first deploy, or does the HOA provide a CSV that we import? Build-time question for Todd.
5. **HOA contact email.** Set in `config.hoa_contact_email` at first deploy.
6. **Whether to keep Gmail OAuth client id/secret in env vs DB.** Current default: env for bootstrap simplicity. If Todd prefers fully DB-resident secrets, move both to `config` (encrypted for secret).
7. **Volume size.** Starting at 1 GB. 30 documents at avg 500KB each plus DB and indices is well under 1 GB. Revisit only if document volume grows substantially.

---

End of brief.
