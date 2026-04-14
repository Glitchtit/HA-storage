"""File upload/download endpoints for product and recipe images."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

log = logging.getLogger(__name__)

router = APIRouter(tags=["files"])

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
PRODUCT_IMG_DIR = DATA_DIR / "images" / "products"
PRODUCT_THUMB_DIR = PRODUCT_IMG_DIR / "thumbs"
RECIPE_IMG_DIR = DATA_DIR / "images" / "recipes"

_THUMB_MAX_PX = 128
_THUMB_QUALITY = 75


def _ensure_dirs():
    PRODUCT_IMG_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCT_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    RECIPE_IMG_DIR.mkdir(parents=True, exist_ok=True)


def _thumb_path(filename: str) -> Path:
    """Return the thumbnail path for *filename*, always a .jpg file."""
    return PRODUCT_THUMB_DIR / (Path(filename).stem + ".jpg")


def _make_thumbnail(src: Path, dst: Path) -> bool:
    """Resize *src* to a 128×128 JPEG at *dst*.  Returns True on success."""
    try:
        from PIL import Image  # noqa: PLC0415
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as img:
            img = img.convert("RGB")
            img.thumbnail((_THUMB_MAX_PX, _THUMB_MAX_PX), Image.LANCZOS)
            img.save(dst, "JPEG", quality=_THUMB_QUALITY, optimize=True)
        return True
    except Exception as exc:
        log.warning("Thumbnail generation failed for %s: %s", src.name, exc)
        return False


# ── Product images ─────────────────────────────────────────────────────────

@router.get("/files/products/thumb/{filename}")
def get_product_thumbnail(filename: str):
    """Return a compressed 128×128 JPEG thumbnail.

    Generated and cached on first request; falls back to the full-size image
    if Pillow is unavailable or the image cannot be processed.  This makes
    the endpoint backwards-compatible: existing product images gain thumbnails
    automatically on the first thumbnail request without any migration.
    """
    _ensure_dirs()
    original = PRODUCT_IMG_DIR / filename
    if not original.exists():
        raise HTTPException(404, f"Image '{filename}' not found")
    thumb = _thumb_path(filename)
    if not thumb.exists():
        if not _make_thumbnail(original, thumb):
            return FileResponse(original)
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


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
    # Eagerly generate thumbnail so the first list load after upload is fast.
    _make_thumbnail(path, _thumb_path(filename))
    return {"filename": filename, "size": len(body)}


@router.delete("/files/products/{filename}", status_code=204)
def delete_product_image(filename: str):
    path = PRODUCT_IMG_DIR / filename
    if path.exists():
        path.unlink()
    thumb = _thumb_path(filename)
    if thumb.exists():
        thumb.unlink()


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
