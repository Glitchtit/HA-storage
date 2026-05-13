"""Receipt OCR — parse a grocery receipt image with Claude vision, then
fuzzy-match each line to existing products in the database.

The matcher is intentionally simple: token overlap weighted by length, then
SequenceMatcher as a tie-breaker. Receipts have short cryptic strings like
"KAURAHIUTALE 1KG" that resolve cleanly with token-level matching; full
fuzzy search is overkill.

This module owns the prompt. If you change the JSON shape Claude returns,
update ``models.ReceiptParseResponse`` and the frontend confirmation sheet
in lock-step.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

from ai_client import call_ai_vision_json

log = logging.getLogger(__name__)


_RECEIPT_PROMPT = """\
You are reading a Finnish grocery receipt (likely K-Ruoka, S-market, Lidl, or
Prisma). Extract every line item.

Return ONLY a JSON object with this exact shape:

{
  "store": "K-Ruoka" | "S-market" | "Lidl" | "Prisma" | "unknown",
  "date": "YYYY-MM-DD" or null,
  "lines": [
    {
      "raw_text": "<exact product line as printed>",
      "qty": <number, parsed from the line — default 1 if absent>,
      "unit": "kpl" | "kg" | "g" | "l" | "ml" | null,
      "price": <number in EUR, or null if not visible>
    }
  ]
}

Rules:
- Skip non-product rows: subtotals, discounts, totals, tax lines, loyalty
  card numbers, store address, cashier ID.
- A line like "MAITO 1L 1,29" → raw_text="MAITO 1L", qty=1, unit="l", price=1.29.
- A line like "BANAANI 0,890 KG 2,00" → raw_text="BANAANI", qty=0.89, unit="kg",
  price=2.00.
- If qty is unclear, default to 1 with unit "kpl".
- Keep raw_text in the original Finnish exactly as printed (uppercase
  preserved). Do not translate.
- Return valid JSON only — no markdown fences, no commentary."""


def parse_receipt(image_b64: str, mime_type: str, conn) -> dict[str, Any]:
    """Call vision AI to extract structured line items from a receipt image.

    Returns the raw shape Claude produced (validated against the contract
    above by the caller). Raises ValueError on AI/config failures.
    """
    result = call_ai_vision_json(_RECEIPT_PROMPT, image_b64, mime_type, conn)
    if not isinstance(result, dict):
        raise ValueError("Vision response was not a JSON object")
    result.setdefault("store", "unknown")
    result.setdefault("date", None)
    result.setdefault("lines", [])
    if not isinstance(result["lines"], list):
        raise ValueError("Vision response 'lines' was not a list")
    return result


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-zÅÄÖåäö]{2,}")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _score(raw_tokens: list[str], product_tokens: list[str], product_name: str, raw_text: str) -> float:
    """0..1 score. Token overlap is the main signal; SequenceMatcher on the
    full strings breaks ties when overlap is the same."""
    if not raw_tokens or not product_tokens:
        return 0.0
    raw_set = set(raw_tokens)
    prod_set = set(product_tokens)
    overlap = len(raw_set & prod_set)
    if overlap == 0:
        # Substring fallback: scanned text contained as a prefix of product name
        if any(rt in product_name.lower() for rt in raw_tokens):
            overlap = 0.5
        else:
            return 0.0
    token_score = overlap / max(len(raw_set), len(prod_set))
    string_score = SequenceMatcher(None, raw_text.lower(), product_name.lower()).ratio()
    # Weighted: token overlap dominates, string similarity nudges.
    return 0.7 * token_score + 0.3 * string_score


def match_lines_to_products(
    lines: list[dict[str, Any]],
    conn,
    *,
    min_confidence: float = 0.45,
) -> list[dict[str, Any]]:
    """Add ``suggested_product_id`` and ``confidence`` fields to each line.

    For each line.raw_text, score every active product and pick the best.
    Below ``min_confidence`` the suggestion is dropped (line stays unmatched
    so the user can choose manually or queue it for discovery).
    """
    products = conn.execute(
        "SELECT id, name, unit_id FROM products WHERE active = 1 ORDER BY id"
    ).fetchall()
    indexed = [
        (p["id"], p["name"], p["unit_id"], _tokenize(p["name"]))
        for p in products
    ]

    enriched: list[dict[str, Any]] = []
    for line in lines:
        raw_text = str(line.get("raw_text") or "").strip()
        raw_tokens = _tokenize(raw_text)
        best_id: int | None = None
        best_unit: int | None = None
        best_score = 0.0
        if raw_tokens:
            for pid, pname, punit, ptokens in indexed:
                s = _score(raw_tokens, ptokens, pname, raw_text)
                if s > best_score:
                    best_score = s
                    best_id = pid
                    best_unit = punit
        suggested = best_id if best_score >= min_confidence else None
        enriched.append({
            "raw_text": raw_text,
            "qty": float(line.get("qty") or 1),
            "unit": line.get("unit"),
            "price": line.get("price"),
            "suggested_product_id": suggested,
            "suggested_unit_id": best_unit if suggested else None,
            "confidence": round(best_score, 2),
        })
    return enriched
