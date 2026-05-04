"""Chat orchestration: rate-limit gate, retrieval, Anthropic call, fallbacks.

Per plan-stage decision 3, retrieval lives in services/retrieval.py.
This module is the orchestrator + the Anthropic call + the prompt
shape. Fallback rendering happens in routes/public.py::api_chat where
the exception types defined here are caught and turned into JSON.

Exception hierarchy:
- ChatServiceError: umbrella; never raised directly
  - ChatRateLimited: per-address 50/24h limit hit
  - ChatNoChunks: chunks table is empty (admin still onboarding)
  - ChatServiceUnavailable: Voyage or Anthropic call failed

System prompt anchors the model to the retrieved chunks and tells it
to cite document titles + decline gracefully. The model is allowed to
say "I don't see that in the documents" rather than hallucinate.
"""

from __future__ import annotations

import logging
import os
import sqlite3

import anthropic

from db.connection import transaction
from services import rate_limit, retrieval
from services.embeddings import EmbeddingsUnavailable

logger = logging.getLogger(__name__)


# COST FLAG: Anthropic Haiku, ~$0.001 per chat at typical context size.
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"
_MAX_OUTPUT_TOKENS = 1024
_TOP_K = 6


# === Exception hierarchy ===

class ChatServiceError(Exception):
    """Umbrella; never raised directly."""


class ChatRateLimited(ChatServiceError):
    """Address has exceeded the 50/24h chat limit."""


class ChatNoChunks(ChatServiceError):
    """No active chunks; the bot is still being set up."""


class ChatServiceUnavailable(ChatServiceError):
    """Voyage or Anthropic call failed; render the friendly fallback."""


# === Helpers ===

def _model_id() -> str:
    return os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)


def _api_key() -> str:
    """Pull the Anthropic key from env. Empty string -> ChatServiceUnavailable."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ChatServiceUnavailable("ANTHROPIC_API_KEY env var is not set")
    return key


def _build_system_prompt(chunks: list[dict], community_name: str = "your HOA") -> str:
    """Compose the system prompt with retrieved context."""
    if not chunks:
        # Defensive; the caller should have raised ChatNoChunks before this.
        context_block = "(no documents are loaded; politely decline to answer)"
    else:
        sections: list[str] = []
        for i, ch in enumerate(chunks, start=1):
            sections.append(
                f"[{i}] (from \"{ch['title']}\")\n{ch['text']}"
            )
        context_block = "\n\n".join(sections)

    return (
        "You are the assistant for "
        f"{community_name}. Answer questions about the HOA's governing "
        "documents and recent correspondence using ONLY the context "
        "below. If the answer is not in the context, say so plainly and "
        "suggest the resident email the HOA. When you do answer, cite "
        "the document title in your reply (for example: \"Per the CCRs, "
        "...\"). Keep answers concise and practical.\n\n"
        "=== Context ===\n"
        f"{context_block}\n"
        "=== End context ==="
    )


def _call_anthropic(
    system_prompt: str,
    history: list[dict],
    user_msg: str,
) -> str:
    """Make the Anthropic Messages API call. Raises ChatServiceUnavailable on any failure.

    `history` is the in-cookie last 6 message pairs (12 entries). The
    Anthropic Messages format requires alternating user/assistant; the
    routes layer guarantees this shape (it appends pairs only).
    """
    try:
        client = anthropic.Anthropic(api_key=_api_key())
        # Filter history to known shape; defensive against cookie tampering.
        msgs: list[dict] = []
        for entry in history:
            role = entry.get("role")
            content = entry.get("content") or ""
            if role in ("user", "assistant") and content:
                msgs.append({"role": role, "content": content})
        msgs.append({"role": "user", "content": user_msg})

        resp = client.messages.create(
            model=_model_id(),
            max_tokens=_MAX_OUTPUT_TOKENS,
            system=system_prompt,
            messages=msgs,
        )
        # The Messages API returns content as a list of content blocks; we
        # concatenate the text-typed ones.
        parts: list[str] = []
        for block in resp.content or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts).strip() or "(no response)"
    except anthropic.APIError as exc:
        raise ChatServiceUnavailable(f"Anthropic API error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        # Generic catch: any client-init/network/decoding issue we missed.
        raise ChatServiceUnavailable(f"Anthropic call failed: {exc}") from exc


# === Conversation persistence ===

def _ensure_conversation(
    conn: sqlite3.Connection,
    session_id: str,
    normalized_address: str,
) -> int:
    """Find or create a conversation row for this session+address.

    A session+address pair gets one conversations row. Returns its id.
    """
    row = conn.execute(
        """
        SELECT id FROM conversations
         WHERE session_id = ? AND normalized_address = ?
         ORDER BY id DESC LIMIT 1;
        """,
        (session_id, normalized_address),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO conversations (session_id, normalized_address) VALUES (?, ?);",
        (session_id, normalized_address),
    )
    return int(cur.lastrowid)


def _persist_messages(
    conn: sqlite3.Connection,
    conv_id: int,
    user_msg: str,
    assistant_msg: str,
) -> None:
    """Insert the user msg and the assistant msg as two rows."""
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?);",
        (conv_id, user_msg),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?);",
        (conv_id, assistant_msg),
    )


# === Public entry point ===

def handle_chat(
    conn: sqlite3.Connection,
    normalized_address: str,
    user_msg: str,
    history: list[dict],
    session_id: str,
) -> dict:
    """Run the full chat pipeline.

    Returns:
        {
            "response": str,
            "cited_documents": [str, ...],
        }

    Raises one of the ChatServiceError subclasses; routes/public.py
    catches each and renders the friendly fallback shape.
    """
    if not normalized_address:
        # Should not happen because the route is gated; defensive.
        raise ChatServiceUnavailable("missing normalized_address (session bug)")

    # 1. Rate-limit gate.
    if not rate_limit.check_chat_rate(conn, normalized_address):
        raise ChatRateLimited()

    # 2. Empty-chunks check (binding-scope item 6 / brief 6.2 fallback).
    if retrieval.chunk_count(conn) == 0:
        raise ChatNoChunks()

    # 3. Retrieve top chunks (Voyage call). Catch any embeddings failure.
    try:
        chunks = retrieval.top_k(conn, user_msg, k=_TOP_K)
    except EmbeddingsUnavailable as exc:
        raise ChatServiceUnavailable(f"embeddings: {exc}") from exc

    if not chunks:
        # Active documents but no chunks at all in the join; treat as no-chunks.
        raise ChatNoChunks()

    # 4. Anthropic call.
    from services import config_store
    community_name = config_store.get(
        conn, config_store.KEY_COMMUNITY_NAME, "your HOA"
    ) or "your HOA"
    system_prompt = _build_system_prompt(chunks, community_name=community_name)
    response = _call_anthropic(system_prompt, history, user_msg)

    # 5. Persist messages + record rate-limit row in a single transaction.
    with transaction(conn):
        conv_id = _ensure_conversation(conn, session_id, normalized_address)
        _persist_messages(conn, conv_id, user_msg, response)
        rate_limit.record_chat(conn, normalized_address)

    cited = sorted({ch["title"] for ch in chunks})
    return {
        "response": response,
        "cited_documents": cited,
    }
