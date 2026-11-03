"""
function_app.py — HTTP-triggered Azure Function wrapping the existing
statement pipeline (parser.py -> fx.py -> build_pipeline.py) behind a single
upload endpoint.

    POST /api/upload-statement
      Body:    raw PDF bytes, Content-Type: application/pdf (or
               application/octet-stream)
      Header:  X-Filename (optional) - original filename to record and to
               archive the PDF under; a generated name is used if omitted.
      Auth:    function key (authLevel=FUNCTION) - pass as ?code=<key> or
               the x-functions-key header.

    Response: 200 with a JSON summary on success; 400/413 for a bad request;
    422 if the PDF failed to parse/load; 500 on anything unexpected.

Each invocation opens its own Azure SQL connection (BP.get_conn()) and closes
it in `finally`, so concurrent requests don't share connection/transaction
state. build_pipeline.load_statement() only commits once, at the very end,
so any exception during parsing/inserting is rolled back cleanly here -
either a statement is fully loaded, or nothing is persisted for it.
"""
import json
import logging
import os
import tempfile
import uuid

import azure.functions as func

import build_pipeline as BP

app = func.FunctionApp()

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 25 * 1024 * 1024))  # 25 MB default
BLOB_CONTAINER = os.environ.get("STATEMENTS_CONTAINER", "statements")
BLOB_CONNECTION_ENV = "STATEMENTS_STORAGE_CONNECTION_STRING"


def _json_response(payload, status_code):
    return func.HttpResponse(
        json.dumps(payload, default=str),
        status_code=status_code,
        mimetype="application/json",
    )


def _archive_to_blob(pdf_bytes, blob_name):
    """Uploads the original PDF to blob storage; returns the blob URL, or
    None if archival storage isn't configured (so local/dev runs without a
    storage account don't fail the whole request)."""
    conn_str = os.environ.get(BLOB_CONNECTION_ENV)
    if not conn_str:
        logging.warning("%s not set; skipping PDF archive.", BLOB_CONNECTION_ENV)
        return None

    from azure.storage.blob import BlobServiceClient, ContentSettings

    service = BlobServiceClient.from_connection_string(conn_str)
    container = service.get_container_client(BLOB_CONTAINER)
    try:
        container.create_container()
    except Exception:
        pass  # already exists

    blob = container.get_blob_client(blob_name)
    blob.upload_blob(
        pdf_bytes, overwrite=True,
        content_settings=ContentSettings(content_type="application/pdf"),
    )
    return blob.url


@app.function_name(name="upload_statement")
@app.route(route="upload-statement", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def upload_statement(req: func.HttpRequest) -> func.HttpResponse:
    content_type = (req.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type not in ("application/pdf", "application/octet-stream"):
        return _json_response(
            {"error": f"Expected Content-Type application/pdf, got '{content_type or 'none'}'."},
            400,
        )

    body = req.get_body()
    if not body:
        return _json_response({"error": "Empty request body."}, 400)
    if len(body) > MAX_UPLOAD_BYTES:
        return _json_response(
            {"error": f"Upload exceeds max size of {MAX_UPLOAD_BYTES} bytes."}, 413
        )
    if not body.startswith(b"%PDF"):
        return _json_response(
            {"error": "Body does not look like a PDF (missing %PDF header)."}, 400
        )

    filename = req.headers.get("X-Filename") or f"upload-{uuid.uuid4().hex}.pdf"

    tmp_path = None
    conn = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(body)
            tmp_path = tmp.name

        conn = BP.get_conn()

        try:
            statement_id, reconciled, fx_source, used_fx_fallback = BP.load_statement(
                conn, tmp_path, filename=filename
            )
        except Exception as exc:
            conn.rollback()
            logging.exception("Failed to load statement %s", filename)
            return _json_response(
                {"error": "Failed to parse or load this statement.", "detail": str(exc)}, 422
            )

        dict_cur = conn.cursor(as_dict=True)
        dict_cur.execute(
            "SELECT account_number, statement_month, parse_status FROM Statements "
            "WHERE statement_id=%s",
            (statement_id,),
        )
        row = dict_cur.fetchone()
        account_number, statement_month, parse_status = (
            row["account_number"], row["statement_month"], row["parse_status"]
        )

        blob_url = None
        try:
            blob_name = f"{account_number or 'unknown'}/{statement_month or 'unknown'}/{filename}"
            blob_url = _archive_to_blob(body, blob_name)
            if blob_url:
                conn.cursor().execute(
                    "UPDATE Statements SET file_path=%s WHERE statement_id=%s",
                    (blob_url, statement_id),
                )
                conn.commit()
        except Exception:
            # The statement is already loaded successfully; don't fail the
            # whole request just because the archive copy didn't make it.
            logging.exception("Statement %s loaded but PDF archive to blob failed.", statement_id)

        dict_cur.execute(
            "SELECT warning_type, warning, severity FROM ParseWarnings WHERE statement_id=%s",
            (statement_id,),
        )
        warnings = list(dict_cur.fetchall())

        return _json_response(
            {
                "statement_id": statement_id,
                "account_number": account_number,
                "statement_month": statement_month,
                "parse_status": parse_status,
                "reconciled": reconciled,
                "fx_source": fx_source,
                "used_fx_fallback": used_fx_fallback,
                "file_archived": blob_url is not None,
                "warnings": warnings,
            },
            200,
        )
    finally:
        if conn is not None:
            conn.close()
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
