"""Linker HTTP surface: reconcile sweep + link-proposal review queue."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

import linker

log = logging.getLogger(__name__)
router = APIRouter()


def _get_db():
    from main import get_connection
    return get_connection()


@router.post("/products/reconcile")
def reconcile_products():
    """Idempotent sweep: backfill pack conversions and (re)link orphan products."""
    conn = _get_db()
    return linker.run_reconcile(conn)


@router.get("/link-proposals")
def list_link_proposals():
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT lp.id, lp.product_id, p.name AS product_name,
               lp.proposed_parent_id, pp.name AS proposed_parent_name,
               lp.confidence, lp.created_at
        FROM link_proposals lp
        JOIN products p  ON p.id  = lp.product_id
        JOIN products pp ON pp.id = lp.proposed_parent_id
        WHERE lp.status = 'pending'
        ORDER BY lp.created_at ASC, lp.id ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/link-proposals/{proposal_id}/accept")
def accept_link_proposal(proposal_id: int):
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM link_proposals WHERE id = ? AND status = 'pending'",
        (proposal_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Pending proposal {proposal_id} not found")
    try:
        linker.apply_link(
            conn, row["product_id"], row["proposed_parent_id"],
            note="link proposal accepted",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    conn.execute(
        "UPDATE link_proposals SET status = 'accepted' WHERE id = ?", (proposal_id,)
    )
    conn.commit()
    return {"ok": True}


@router.post("/link-proposals/{proposal_id}/reject")
def reject_link_proposal(proposal_id: int):
    conn = _get_db()
    cur = conn.execute(
        "UPDATE link_proposals SET status = 'rejected' WHERE id = ? AND status = 'pending'",
        (proposal_id,),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, f"Pending proposal {proposal_id} not found")
    return {"ok": True}
