"""Admin document management: upload, paste, toggle, reembed, paste-text."""

from __future__ import annotations

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from db.connection import get_db
from routes.admin import admin_bp
from routes.admin._common import require_admin
from services import documents as docs_svc
from services.csrf import require_csrf


@admin_bp.route("/documents", methods=["GET"])
@require_admin
def documents_list():
    """Render the documents table."""
    conn = get_db()
    return render_template(
        "admin/documents.html", documents=docs_svc.list_documents(conn)
    )


@admin_bp.route("/documents/upload", methods=["POST"])
@require_csrf
@require_admin
def documents_upload():
    """Accept a PDF upload, ingest it, redirect back."""
    conn = get_db()
    file = request.files.get("pdf")
    title = (request.form.get("title") or "").strip() or "Untitled document"
    if not file or not file.filename:
        flash("Choose a PDF file to upload.", "error")
        return redirect(url_for("admin.documents_list"))

    file_bytes = file.read()
    if not file_bytes:
        flash("Uploaded file was empty.", "error")
        return redirect(url_for("admin.documents_list"))

    try:
        result = docs_svc.ingest_pdf_bytes(
            conn=conn,
            data_dir=current_app.config["DATA_DIR"],
            title=title,
            file_bytes=file_bytes,
            source="admin_upload",
        )
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("PDF upload failed: %s", exc, exc_info=True)
        flash(f"Upload failed: {exc}", "error")
        return redirect(url_for("admin.documents_list"))

    if result["needs_ocr"]:
        flash(
            f'"{title}" was uploaded but pypdf could not extract text '
            f"(possibly scanned). Paste the text below the row, then "
            f"click reembed.",
            "info",
        )
    else:
        flash(
            f'"{title}" uploaded with {result["chunks"]} chunk(s).',
            "success",
        )
    return redirect(url_for("admin.documents_list"))


@admin_bp.route("/documents/paste", methods=["POST"])
@require_csrf
@require_admin
def documents_paste():
    """Accept pasted text as a new document."""
    conn = get_db()
    title = (request.form.get("title") or "").strip()
    full_text = (request.form.get("full_text") or "").strip()
    if not title or not full_text:
        flash("Title and text are both required.", "error")
        return redirect(url_for("admin.documents_list"))

    try:
        result = docs_svc.ingest_text(
            conn=conn,
            title=title,
            full_text=full_text,
            source="admin_paste",
        )
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("Text ingest failed: %s", exc, exc_info=True)
        flash(f"Ingest failed: {exc}", "error")
        return redirect(url_for("admin.documents_list"))

    flash(f'"{title}" saved with {result["chunks"]} chunk(s).', "success")
    return redirect(url_for("admin.documents_list"))


@admin_bp.route("/documents/<int:doc_id>/toggle", methods=["POST"])
@require_csrf
@require_admin
def document_toggle(doc_id):
    """Flip is_active on a document."""
    conn = get_db()
    try:
        new_val = docs_svc.toggle_active(conn, doc_id)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.documents_list"))
    flash(f"Document {doc_id} is_active = {new_val}.", "info")
    return redirect(url_for("admin.documents_list"))


@admin_bp.route("/documents/<int:doc_id>/reembed", methods=["POST"])
@require_csrf
@require_admin
def document_reembed(doc_id):
    """Drop chunks and re-embed from full_text."""
    conn = get_db()
    try:
        n = docs_svc.reembed_document(conn, doc_id)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("reembed failed: %s", exc, exc_info=True)
        flash(f"Reembed failed: {exc}", "error")
        return redirect(url_for("admin.documents_list"))
    flash(f"Re-embedded {n} chunk(s) for document {doc_id}.", "success")
    return redirect(url_for("admin.documents_list"))


@admin_bp.route("/documents/<int:doc_id>/text", methods=["POST"])
@require_csrf
@require_admin
def document_paste_text(doc_id):
    """Update an existing document's full_text (post-OCR rescue)."""
    conn = get_db()
    full_text = (request.form.get("full_text") or "").strip()
    if not full_text:
        flash("Pasted text was empty.", "error")
        return redirect(url_for("admin.documents_list"))
    docs_svc.update_full_text(conn, doc_id, full_text)
    flash(
        f"Updated text for document {doc_id}. Click reembed to refresh chunks.",
        "info",
    )
    return redirect(url_for("admin.documents_list"))
