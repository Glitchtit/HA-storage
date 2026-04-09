"""File upload/download endpoints for product and recipe images."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(tags=["files"])

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
PRODUCT_IMG_DIR = DATA_DIR / "images" / "products"
RECIPE_IMG_DIR = DATA_DIR / "images" / "recipes"


def _ensure_dirs():
    PRODUCT_IMG_DIR.mkdir(parents=True, exist_ok=True)
    RECIPE_IMG_DIR.mkdir(parents=True, exist_ok=True)


# ── Product images ─────────────────────────────────────────────────────────

@router.get("/files/products/{filename}")
def get_product_image(filename: str):
    _ensure_dirs()
    path = PRODUCT_IMG_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"Image '{filename}' not found")
    return FileResponse(path)


@router.put("/files/products/{filename}", status_code=201)
async def upload_product_image(filename: str, request: Request):
    _ensure_dirs()
    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty body")
    path = PRODUCT_IMG_DIR / filename
    path.write_bytes(body)
    return {"filename": filename, "size": len(body)}


@router.delete("/files/products/{filename}", status_code=204)
def delete_product_image(filename: str):
    path = PRODUCT_IMG_DIR / filename
    if path.exists():
        path.unlink()


# ── Recipe images ──────────────────────────────────────────────────────────

@router.get("/files/recipes/{filename}")
def get_recipe_image(filename: str):
    _ensure_dirs()
    path = RECIPE_IMG_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"Image '{filename}' not found")
    return FileResponse(path)


@router.put("/files/recipes/{filename}", status_code=201)
async def upload_recipe_image(filename: str, request: Request):
    _ensure_dirs()
    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty body")
    path = RECIPE_IMG_DIR / filename
    path.write_bytes(body)
    return {"filename": filename, "size": len(body)}


@router.delete("/files/recipes/{filename}", status_code=204)
def delete_recipe_image(filename: str):
    path = RECIPE_IMG_DIR / filename
    if path.exists():
        path.unlink()
