"""Ingredient resolver endpoint (spec §4.6): HA-recipes sends parsed recipe
lines, Storage answers with the concept, amount and unit for each."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["ingredients"])


def _get_db():
    from main import get_connection
    return get_connection()


class ResolveItem(BaseModel):
    raw: str | None = None          # the source line, kept by the caller as raw_text
    name: str | None = None         # generic Finnish name ("kerma")
    specific: str | None = None     # variant ("vispikerma") or null
    amount: float | None = None
    unit_text: str | None = None
    source_name: str | None = None  # the word in the source language ("vispgrädde")


class ResolveRequest(BaseModel):
    items: list[ResolveItem]


@router.post("/ingredients/resolve")
def resolve_ingredients(body: ResolveRequest):
    from ai_client import call_ai_json
    from ingredient_resolver import resolve
    from routers.products import _ai_configured

    conn = _get_db()
    ai = call_ai_json if _ai_configured(conn) else None
    return resolve(conn, [i.model_dump() for i in body.items], ai=ai)
