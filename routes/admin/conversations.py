"""Admin conversation history: list + view transcript."""

from __future__ import annotations

from flask import flash, redirect, render_template, url_for

from db.connection import get_db
from routes.admin import admin_bp
from routes.admin._common import require_admin


@admin_bp.route("/conversations", methods=["GET"])
@require_admin
def conversations_list():
    """List recent conversations with first-message preview + message count."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT
            c.id, c.normalized_address, c.created_at,
            (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS msg_count,
            (SELECT content FROM messages m WHERE m.conversation_id = c.id
              ORDER BY m.id LIMIT 1) AS first_msg
          FROM conversations c
         ORDER BY c.id DESC
         LIMIT 100;
        """
    ).fetchall()
    return render_template("admin/conversations.html", conversations=rows)


@admin_bp.route("/conversations/<int:conv_id>", methods=["GET"])
@require_admin
def conversation_view(conv_id):
    """Full transcript of one conversation."""
    conn = get_db()
    conv = conn.execute(
        "SELECT * FROM conversations WHERE id = ?;",
        (conv_id,),
    ).fetchone()
    if not conv:
        flash("Conversation not found.", "error")
        return redirect(url_for("admin.conversations_list"))
    msgs = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id;",
        (conv_id,),
    ).fetchall()
    return render_template(
        "admin/conversation_view.html", conv=conv, messages=msgs
    )
