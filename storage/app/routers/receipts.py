"""Receipt OCR endpoints — parse a grocery receipt image with vision AI and
batch-add the confirmed line items to stock.

Flow:
  1. Client POSTs base64-encoded image to ``/api/receipts/parse``.
  2. Server returns parsed lines with product suggestions and confidences.
  3. Client shows confirmation sheet; user edits and confirms.
  4. Client POSTs the confirmed lines to ``/api/receipts/commit``.
  5. Server creates stock entries via the same path ``/stock/add`` uses.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from models import (
    ReceiptCommitRequest,
    ReceiptCommitResponse,
    ReceiptParseRequest,
    ReceiptParseResponse,
)
from receipt_parser import match_lines_to_products, parse_receipt
from routers.history import log_event

router = APIRouter(tags=["receipts"])
log = logging.getLogger(__name__)


def _get_db():
    from main import get_connection
    return get_connection()


@router.post("/receipts/parse", response_model=ReceiptParseResponse)
def parse_receipt_endpoint(body: ReceiptParseRequest):
    """Send the image to vision AI and return parsed+matched lines.

    Returns 503 when AI is misconfigured (e.g. claude_api_key missing), 400 on
    bad input, 502 when the vision call fails after retries.
    """
    if not body.image_b64:
        raise HTTPException(400, "image_b64 is required")
    if not body.mime_type.startswith("image/"):
        raise HTTPException(400, f"mime_type must be image/*, got {body.mime_type!r}")

    conn = _get_db()
    try:
        parsed = parse_receipt(body.image_b64, body.mime_type, conn)
    except ValueError as exc:
        msg = str(exc)
        if "api_key" in msg or "ai_provider" in msg:
            raise HTTPException(503, msg) from exc
        raise HTTPException(502, msg) from exc

    enriched = match_lines_to_products(parsed.get("lines", []), conn)
    return {
        "store": parsed.get("store", "unknown"),
        "date": parsed.get("date"),
        "lines": enriched,
    }


@router.post("/receipts/commit", response_model=ReceiptCommitResponse)
def commit_receipt_endpoint(body: ReceiptCommitRequest):
    """Batch-add confirmed receipt lines to stock.

    Each line creates a new stock row (no auto-FIFO merging — matches the
    existing /stock/add semantics). Continues past per-line failures so a
    single bad product_id doesn't kill the whole receipt; errors are
    aggregated in the response.
    """
    conn = _get_db()
    added = 0
    errors: list[str] = []
    for idx, line in enumerate(body.lines):
        try:
            product = conn.execute(
                "SELECT id, unit_id, location_id, default_best_before_days, unit_price "
                "FROM products WHERE id = ?",
                (line.product_id,),
            ).fetchone()
            if not product:
                errors.append(f"Line {idx}: product {line.product_id} not found")
                continue

            location_id = line.location_id or product["location_id"]
            if location_id is None:
                fallback = conn.execute(
                    "SELECT id FROM locations ORDER BY id LIMIT 1"
                ).fetchone()
                if not fallback:
                    errors.append(f"Line {idx}: no locations defined")
                    continue
                location_id = fallback["id"]

            unit_id = line.unit_id or product["unit_id"]
            best_before = None
            if product["default_best_before_days"]:
                row = conn.execute(
                    "SELECT date('now', '+' || ? || ' days') AS d",
                    (product["default_best_before_days"],),
                ).fetchone()
                best_before = row["d"]

            # Receipt lines may include a per-line total price. Convert to per-unit
            # for the lot snapshot. Fall back to the product's default unit_price.
            price_paid = None
            if line.price_paid is not None and line.amount > 0:
                price_paid = float(line.price_paid) / float(line.amount)
            if price_paid is None:
                price_paid = product.get("unit_price")

            cur = conn.execute(
                "INSERT INTO stock (product_id, location_id, amount, unit_id, "
                "best_before_date, price_paid) VALUES (?, ?, ?, ?, ?, ?)",
                (line.product_id, location_id, line.amount, unit_id, best_before, price_paid),
            )
            log_event(
                conn,
                product_id=line.product_id,
                event_type="purchase",
                amount=line.amount,
                unit_id=unit_id,
                location_id=location_id,
                stock_id=cur.lastrowid,
                note=line.note or "receipt",
                unit_price=price_paid,
            )
            added += 1
        except Exception as exc:
            errors.append(f"Line {idx}: {exc}")

    conn.commit()
    log.info("Receipt commit: added=%d, failed=%d", added, len(errors))
    return {"added": added, "failed": len(errors), "errors": errors}
