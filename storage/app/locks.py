"""Process-wide catalog lock (spec §4.7).

Storage runs as one uvicorn process, so a single re-entrant lock serializes
every catalog change that must not interleave with another: resolving recipe
ingredients into concepts, creating and re-parenting products, adopting SKUs
and merging. Hold it around database work only — never across an AI call,
which can take minutes.
"""
from __future__ import annotations

import threading

CATALOG_LOCK = threading.RLock()
