# HOA Chatbot

A small Flask web app that answers homeowner questions about a single HOA's
governing documents. Address-gated chat, admin panel, Gmail ingestion of HOA
correspondence, server-rendered Jinja, SQLite on a Railway volume.

Single-tenant, single-board admin. Not a SaaS. v2 may add multi-tenancy.

See `BUILD_BRIEF.md` and `IMPLEMENTATION_PLAN.md` for the full spec.

---

## Stack

- Python 3.11, Flask 3.x, gunicorn (1 worker, 4 threads).
- SQLite + WAL on a Railway 1GB volume mount at `/data`.
- APScheduler in-process (gmail ingest every 15 min, retention sweep daily).
- Voyage embeddings + numpy cosine for retrieval (top K=6).
- Anthropic Haiku for chat completion.
- Fernet (cryptography) for at-rest encryption of the Gmail refresh token.
- bcrypt for the admin password.
- Gmail API + OAuth refresh-token flow for HOA correspondence ingest.
- Server-rendered Jinja templates + a single vanilla-JS `chat.js` (no React, no build step).
- pypdf for PDF text extraction (no OCR in v1).

`COST FLAG:` Anthropic Haiku, Voyage embeddings, Railway compute, Railway volume. Estimated $5 to $10/mo.

---

## Local development

Prereqs: Python 3.11 and a virtualenv tool of choice. Optional but recommended:
the `cryptography` and `bcrypt` wheels build cleanly on macOS / Linux / WSL2.

1. Clone, `cd` into the repo, create a virtualenv:

    ```sh
    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

2. Copy the env template and fill in the values you need for local dev:

    ```sh
    cp .env.example .env
    ```

   Generate values for the empty fields:

    ```sh
    python -c "import secrets; print(secrets.token_urlsafe(32))"
    # paste into FLASK_SECRET_KEY

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # paste into ENCRYPTION_KEY
    ```

   For local dev you can leave `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, and the
   Gmail OAuth fields at their `PLACEHOLDER` values; the chat path will hit
   the friendly "service unavailable" fallback instead of erroring out.

   Set `FLASK_ENV=development` to relax the Secure cookie flag for plain HTTP.

3. Initialize the database:

    ```sh
    python scripts/init_db.py
    ```

   This applies migrations and seeds non-secret config keys with placeholders.
   Idempotent; safe to re-run.

4. Set the admin password:

    ```sh
    python scripts/set_admin_password.py
    ```

5. Run the dev server:

    ```sh
    flask --app "app:create_app" run --debug
    ```

   The address gate is at `/`. Admin login is at `/admin/login`.

   To skip the APScheduler boot during scripts or tests, set
   `FLASK_SKIP_SCHEDULER=1`.

---

## Environment variables

See `.env.example`. Required values:

| Var | Source | Notes |
|---|---|---|
| `FLASK_SECRET_KEY` | generated | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ENCRYPTION_KEY` | generated | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Rotating breaks every encrypted row. |
| `DATABASE_PATH` | local: `./hoa_bot.db`. Railway: `/data/hoa_bot.db`. | |
| `DATA_DIR` | local: `./data`. Railway: `/data`. | Base dir for `whitelisted_senders.txt` and seed-only files. |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/ | Paid. ~$0.001 per chat. |
| `ANTHROPIC_MODEL` | constant | Default `claude-haiku-4-5`. Confirm the exact id at deploy time. |
| `VOYAGE_API_KEY` | https://www.voyageai.com/ | Free tier covers v1 traffic. |
| `VOYAGE_MODEL` | constant | Default `voyage-3-lite` (512 dim). |
| `GMAIL_CLIENT_ID` | Google Cloud Console | OAuth Desktop app. |
| `GMAIL_CLIENT_SECRET` | Google Cloud Console | |
| `GMAIL_BOT_ADDRESS` | Gmail address used for HOA ingest. | |
| `HOA_CONTACT_EMAIL` | seed for the `config` table. | Editable via `/admin/config` after deploy. |
| `COMMUNITY_NAME` | seed for the `config` table. | Editable via `/admin/config` after deploy. |
| `LOG_LEVEL` | `INFO` default | |

The Gmail refresh token is NEVER set as an env var. It is written into
`config.value_encrypted` (key `gmail_refresh_token`) by the OAuth bootstrap
script.

The admin password hash is also NEVER set as an env var. It lives in
`config.value` (key `admin_password_hash`) and is written by
`scripts/set_admin_password.py`.

---

## Database

Hand-written SQL migrations live in `db/migrations/*.sql`. The runner in
`db/migrations.py` records applied files in the `applied_migrations` table
and skips them on re-run.

To apply:

```sh
python scripts/init_db.py
```

To seed addresses from a text file (one address per line, blank/`#` lines skipped):

```sh
python scripts/init_db.py --seed-addresses path/to/addresses.txt
```

WAL is enabled on every connection. Writes use a 3-retry backoff
(100/250/500ms) on `OperationalError: database is locked`.

---

## Documents and ingestion

PDFs upload via `/admin/documents`. Text extracts via pypdf; if a PDF
larger than 100KB extracts under 50 chars, it is flagged `needs_ocr` and
not used for retrieval. Admin can paste the text manually via the same
page (no OCR pipeline in v1).

Each document is chunked at ~800 tokens with ~100 token overlap, embedded
via Voyage, and stored in the `chunks` table. Reembedding via the admin
button rewrites all chunks for one document.

Gmail ingest pulls unread, whitelisted, un-labeled messages every 15
minutes and ingests subject + body + attachments. On success the
`hoa-bot-processed` Gmail label is applied; the email is NOT marked as
read (preserves operator inbox state). Dedup uses
`documents.gmail_message_id` UNIQUE plus per-attachment `file_hash`.

---

## Phase 8 deploy runbook (Todd runs this)

This runbook covers the Railway provisioning, GitHub remote setup, first
push, and smoke tests. Run sequentially.

### Step 1: Procure API keys

Before touching Railway:

1. **Anthropic API key**: https://console.anthropic.com/ -> Settings -> API Keys -> Create. `COST FLAG:` paid; expect ~$0.001 per chat for HOA-shaped traffic.
2. **Voyage API key**: https://www.voyageai.com/ -> Dashboard -> API Keys. Free tier (200M tokens / month) is plenty for v1.
3. **Gmail OAuth client (Desktop type)**:
   - Go to https://console.cloud.google.com/.
   - Create or pick a project.
   - Enable the Gmail API for that project.
   - APIs & Services -> Credentials -> Create credentials -> OAuth client ID -> Desktop app.
   - Save the `Client ID` and `Client secret` for later.
   - Configure the OAuth consent screen if prompted; add the bot Gmail address as a test user (no app verification needed for single-tenant use).

### Step 2: Generate secrets locally

```sh
# Flask session signing key
python -c "import secrets; print(secrets.token_urlsafe(32))"
# -> save as FLASK_SECRET_KEY

# Fernet encryption key for the gmail refresh token
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# -> save as ENCRYPTION_KEY
```

### Step 3: Create the Railway project

1. https://railway.app/ -> New Project -> Empty Project.
2. Add a service: connect to GitHub (we push to the repo in step 4) or deploy from local.
3. Service Settings -> Volumes -> New Volume:
   - Mount path: `/data`
   - Size: 1 GB

### Step 4: Set env vars on Railway

Service Settings -> Variables:

```
FLASK_SECRET_KEY=<the value from step 2>
FLASK_ENV=production
DATABASE_PATH=/data/hoa_bot.db
DATA_DIR=/data
ENCRYPTION_KEY=<the value from step 2>
ANTHROPIC_API_KEY=<from step 1>
ANTHROPIC_MODEL=claude-haiku-4-5
VOYAGE_API_KEY=<from step 1>
VOYAGE_MODEL=voyage-3-lite
GMAIL_CLIENT_ID=<from step 1>
GMAIL_CLIENT_SECRET=<from step 1>
GMAIL_BOT_ADDRESS=<your bot Gmail address>
HOA_CONTACT_EMAIL=<your HOA board email>
COMMUNITY_NAME=<your HOA name>
LOG_LEVEL=INFO
```

### Step 5: Configure GitHub remote and first push

Container user is "claude" (UID mismatch with host user "todd"). If git
permission issues hit:

```sh
git config --global --add safe.directory ~/projects/hoa-chatbot
```

Set the SSH remote (never HTTPS):

```sh
cd ~/projects/hoa-chatbot
git remote add origin git@github.com:ToddWelch/hoa-chatbot.git
git push -u origin feat/hoa-chatbot-v1
```

(Optional, after smoke tests pass: merge `feat/hoa-chatbot-v1` to `main`,
push `main`, and either point Railway at `main` or keep deploying off the
feature branch for v1.)

### Step 6: First deploy

Railway picks up the Dockerfile automatically. The CMD chain is
`scripts/start.py` -> migrations via `init_db.py` -> `gunicorn`.

Watch the build logs. On the first successful deploy, hit the Railway-
provided subdomain. `/healthz` should return `{"status": "ok"}`.

### Step 7: Initialize DB explicitly (optional, for clarity)

`scripts/start.py` already runs migrations on every container start, but
running it once explicitly is a good sanity check:

```sh
railway run python scripts/init_db.py
```

If you have a CSV of addresses to seed:

```sh
# Upload the CSV to a path inside the container, e.g. /data/seed.txt,
# OR run init_db locally pointing at the prod DB and copy the result:
railway run python scripts/init_db.py --seed-addresses /data/seed.txt
```

### Step 8: Set the admin password

```sh
railway run python scripts/set_admin_password.py
```

Pick a password at least 8 chars. The script bcrypt-hashes and writes it
to `config.admin_password_hash`.

### Step 9: Bootstrap Gmail OAuth

```sh
railway run python scripts/gmail_oauth_setup.py
```

The script prints an authorization URL. Open it in a browser, grant the
`gmail.modify` scope, copy the redirect code back into the terminal.
The script encrypts the refresh token via Fernet and writes it to
`config.value_encrypted` for `key='gmail_refresh_token'`.

If Google shows "this app isn't verified", click Advanced -> Continue.
v1 is single-user; app verification is not required.

### Step 10: Smoke test

Run through the manual verification checklist from the build:

- [ ] Public URL serves the address-gate page over HTTPS.
- [ ] Submitting a whitelisted address redirects to `/chat`; cookie persists across reload.
- [ ] Submitting a non-whitelisted address renders the friendly fallback with a mailto link; `/admin/failed-addresses` shows the row.
- [ ] Admin login at `/admin/login` works with the password set in step 8; six wrong tries from one IP triggers the friendly cooldown message.
- [ ] Upload a small text-extractable PDF in `/admin/documents`; chunk count appears.
- [ ] Send a chat about the PDF's content; the response cites the document title.
- [ ] Mailto fallback link, when clicked, opens the mail client with subject + body URL-encoded.
- [ ] Send a test email from a whitelisted sender to the bot Gmail; click "test ingest" in `/admin/config`; new row appears in `/admin/documents`; `hoa-bot-processed` label appears on the email.
- [ ] Submit 51 chats from one whitelisted address; attempt 51 returns the rate-limit copy.
- [ ] Soft-delete an uploaded document; subsequent chat answers no longer cite it.
- [ ] Delete every row from `chunks` (`railway run sqlite3 /data/hoa_bot.db "DELETE FROM chunks;"`); chat returns the "still being set up" fallback (NOT a 500). Restore by clicking reembed on a document.
- [ ] Set `ANTHROPIC_API_KEY` to a deliberately invalid value, restart, send a chat; the "service unavailable" fallback appears (NOT a 500). Restore the key.

---

## Operations

### Manual backup

Weekly:

```sh
railway run sqlite3 /data/hoa_bot.db ".backup /data/app.backup.db"
railway run cat /data/app.backup.db > local-backup-$(date +%F).db
```

Or shut the service briefly and snapshot the volume from the Railway dashboard.

### Retention

A daily APScheduler job at 03:00 UTC deletes:

- `messages` rows older than 90 days.
- `conversations` rows with no remaining messages.
- `chat_rate_log` rows older than 48 hours (the rolling window is 24h; 48h gives a buffer for clock skew).

### Encryption key rotation

Rotating `ENCRYPTION_KEY` invalidates every Fernet-encrypted row in the
`config` table. The only such row in v1 is the Gmail refresh token. To
rotate:

1. Set the new key on Railway.
2. Restart the service.
3. Re-run `railway run python scripts/gmail_oauth_setup.py`.

A future rotation helper using `MultiFernet` (read with old, write with
new) is out of scope for v1.

### Embedding model swap

If you change `EMBEDDING_DIM` in `services/embeddings.py`, the boot-time
validator refuses to start until the existing chunk BLOBs match. Procedure:

1. Pause traffic.
2. `railway run sqlite3 /data/hoa_bot.db "DELETE FROM chunks;"`
3. Update `EMBEDDING_DIM` and the model id in `.env` / Railway env.
4. Redeploy. The app boots clean (chunks table is empty).
5. Click "reembed" on each document in `/admin/documents`.

### Gunicorn worker pin

`Procfile` and `Dockerfile` both pin `--workers 1`. Reason: APScheduler
runs in-process; multiple workers would mean duplicate scheduled job runs.
v1 traffic is tiny so the GIL bottleneck on concurrent chats is fine.
v2 fix: external scheduler (Railway cron or a dedicated worker service) and
`--workers 2+`.

---

## Out of scope (v1)

Multi-tenant, React, Postgres, automated tests, OCR, streaming chat, Gmail
Pub/Sub, document versioning, per-resident accounts, translation, mobile
wrapper, analytics dashboard beyond counts, 2FA, automated backups.

See `BUILD_BRIEF.md` section 13 for the full list.
