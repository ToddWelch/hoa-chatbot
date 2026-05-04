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
