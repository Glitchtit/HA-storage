"""Pure quantity engine: unit dimensions, unit-text normalization, conversion.

No I/O and no AI. `facts.py` supplies Unit and Facts objects built from the
database; everything here is deterministic and unit-testable on its own.

Dimensions and their base units:
    mass -> grams, volume -> millilitres, count -> natural pieces,
    package -> retail items, vague -> not convertible (ripaus).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MASS = "mass"
VOLUME = "volume"
COUNT = "count"
PACKAGE = "package"
VAGUE = "vague"
DIMENSIONS = (MASS, VOLUME, COUNT, PACKAGE, VAGUE)

# Plausible density range in g/ml. Anything outside is an AI hallucination
# (live data had a ruokakerma row claiming 10 309 g/ml).
DENSITY_MIN = 0.05
DENSITY_MAX = 5.0

# abbreviation -> (name, plural, dimension, base_factor)
UNIT_CATALOG: dict[str, tuple[str, str, str, float | None]] = {
    "g": ("Gramma", "Grammaa", MASS, 1.0),
    "kg": ("Kilogramma", "Kilogrammaa", MASS, 1000.0),
    "ml": ("Millilitra", "Millilitraa", VOLUME, 1.0),
    "cl": ("Senttilitra", "Senttilitraa", VOLUME, 10.0),
    "dl": ("Desilitra", "Desilitraa", VOLUME, 100.0),
    "l": ("Litra", "Litraa", VOLUME, 1000.0),
    "tl": ("Teelusikka", "Teelusikkaa", VOLUME, 5.0),
    "rkl": ("Ruokalusikka", "Ruokalusikkaa", VOLUME, 15.0),
    "mm": ("Maustemitta", "Maustemittaa", VOLUME, 1.0),
    "kpl": ("Kappale", "Kappaletta", COUNT, 1.0),
    "pkt": ("Paketti", "Pakettia", PACKAGE, 1.0),
    "prk": ("Purkki", "Purkkia", PACKAGE, 1.0),
    "tlk": ("Tölkki", "Tölkkiä", PACKAGE, 1.0),
    "pss": ("Pussi", "Pussia", PACKAGE, 1.0),
    "rasia": ("Rasia", "Rasiaa", PACKAGE, 1.0),
    "nippu": ("Nippu", "Nippua", PACKAGE, 1.0),
    "ruukku": ("Ruukku", "Ruukkua", PACKAGE, 1.0),
    "rs": ("Ripaus", "Ripausta", VAGUE, None),
}

# Canonical name per dimension, used when a display unit must be chosen.
CANONICAL_UNIT = {MASS: "g", VOLUME: "dl", COUNT: "kpl", PACKAGE: "pkt"}

_ALIAS_TABLE: list[tuple[str, float, tuple[str, ...]]] = [
    ("g", 1.0, ("g", "gr", "gram", "gramm", "grams", "gramma", "grammaa")),
    ("kg", 1.0, ("kg", "kilo", "kiloa", "kilogram", "kilogramma", "kilogrammaa")),
    ("ml", 1.0, ("ml", "millilitra", "millilitraa", "milliliter")),
    ("cl", 1.0, ("cl", "senttilitra", "senttilitraa")),
    ("dl", 1.0, ("dl", "desilitra", "desilitraa", "deciliter")),
    ("l", 1.0, ("l", "ltr", "litra", "litraa", "liter", "litre")),
    ("tl", 1.0, ("tl", "teelusikka", "teelusikkaa", "tsk", "tesked", "tsp", "teaspoon")),
    ("rkl", 1.0, ("rkl", "ruokalusikka", "ruokalusikkaa", "msk", "matsked", "tbsp", "tablespoon")),
    ("mm", 1.0, ("mm", "maustemitta", "maustemittaa", "krm", "kryddmått")),
    ("kpl", 1.0, ("kpl", "kappale", "kappaletta", "st", "stk", "styck", "pc", "pcs", "piece", "pieces")),
    ("pkt", 1.0, ("pkt", "paketti", "pakettia", "pakkaus", "pakkausta", "paket", "förp",
                  "förpackning", "pack", "package", "packet")),
    ("prk", 1.0, ("prk", "purkki", "purkkia", "burk", "burkar", "jar")),
    ("tlk", 1.0, ("tlk", "tölkki", "tölkkiä", "can", "tin")),
    ("pss", 1.0, ("pss", "pussi", "pussia", "påse", "påsar", "bag")),
    ("rasia", 1.0, ("rasia", "rasiaa", "ask", "box")),
    ("nippu", 1.0, ("nippu", "nippua", "knippe", "bunch")),
    ("ruukku", 1.0, ("ruukku", "ruukkua", "kruka", "krukor", "pot")),
    ("rs", 1.0, ("rs", "ripaus", "ripausta", "nypa", "nypy", "pinch")),
    ("dl", 2.4, ("cup", "cups")),
    ("g", 28.35, ("oz",)),
    ("g", 453.6, ("lb", "lbs")),
    ("ml", 29.57, ("fl oz",)),
]
UNIT_ALIASES: dict[str, tuple[str, float]] = {
    word: (abbr, mult) for abbr, mult, words in _ALIAS_TABLE for word in words
}


def normalize_unit_text(text: str | None) -> tuple[str, float] | None:
    """Map free unit text ("msk", "Burk", "cups") to (catalog abbreviation, multiplier).

    The multiplier converts an amount written in `text` into the returned unit
    (1 cup = 2.4 dl). Unknown or empty text returns None; callers must not fall
    back to a product's own unit.
    """
    if not text:
        return None
    t = unicodedata.normalize("NFKC", str(text)).strip().casefold().rstrip(".")
    t = re.sub(r"\s+", " ", t)
    return UNIT_ALIASES.get(t)


@dataclass(frozen=True)
class Unit:
    id: int
    abbreviation: str
    dimension: str
    base_factor: float | None


@dataclass(frozen=True)
class Facts:
    content_amount: float | None = None
    content_unit: Unit | None = None
    pack_count: float | None = None
    density_g_ml: float | None = None
    piece_weight_g: float | None = None
    piece_weight_exact: bool = False


@dataclass(frozen=True)
class Converted:
    amount: float
    approx: bool


def plausible_density(value: float | None) -> bool:
    return value is not None and DENSITY_MIN <= float(value) <= DENSITY_MAX


def _edges(facts: Facts, allow_fallback: bool) -> dict[tuple[str, str], tuple[float, bool]]:
    """Directed dimension edges in base units: (from, to) -> (factor, approx)."""
    edges: dict[tuple[str, str], tuple[float, bool]] = {}

    def add(a: str, b: str, factor: float | None, approx: bool) -> None:
        if factor is None or factor <= 0:
            return
        edges.setdefault((a, b), (factor, approx))
        edges.setdefault((b, a), (1.0 / factor, approx))

    cu = facts.content_unit
    if facts.content_amount and cu is not None and cu.base_factor and cu.dimension in (MASS, VOLUME, COUNT):
        add(PACKAGE, cu.dimension, facts.content_amount * cu.base_factor, False)
    if facts.pack_count:
        add(PACKAGE, COUNT, facts.pack_count, False)
    if facts.piece_weight_g:
        add(COUNT, MASS, facts.piece_weight_g, not facts.piece_weight_exact)
    if plausible_density(facts.density_g_ml):
        add(VOLUME, MASS, facts.density_g_ml, True)
    if allow_fallback:
        add(PACKAGE, COUNT, 1.0, True)
    return edges


def _best_path(start: str, goal: str, edges: dict) -> tuple[float, bool] | None:
    """Cheapest path by (approximate edges, edge count); at most 3 edges."""
    best: tuple[tuple[int, int], float] | None = None

    def walk(node: str, seen: frozenset, factor: float, approx_n: int, n: int) -> None:
        nonlocal best
        if node == goal:
            key = (approx_n, n)
            if best is None or key < best[0]:
                best = (key, factor)
            return
        if n == 3:
            return
        for (a, b), (f, approx) in edges.items():
            if a == node and b not in seen:
                walk(b, seen | {b}, factor * f, approx_n + int(approx), n + 1)

    walk(start, frozenset({start}), 1.0, 0, 0)
    if best is None:
        return None
    return best[1], best[0][0] > 0


def convert(
    amount: float,
    from_unit: Unit,
    to_unit: Unit,
    facts: Facts = Facts(),
    *,
    from_dim: str | None = None,
    to_dim: str | None = None,
) -> Converted | None:
    """Convert `amount` of `from_unit` into `to_unit` using product `facts`.

    `from_dim` / `to_dim` override a unit's dimension; callers use them to read
    a SKU's `kpl` as retail packages (see facts.Catalog.stock_dim).
    Returns None when no path exists or either side is vague.
    """
    fd = from_dim or from_unit.dimension
    td = to_dim or to_unit.dimension
    if VAGUE in (fd, td) or not from_unit.base_factor or not to_unit.base_factor:
        return None
    base = float(amount) * from_unit.base_factor
    if fd == td:
        return Converted(base / to_unit.base_factor, False)
    for allow_fallback in (False, True):
        found = _best_path(fd, td, _edges(facts, allow_fallback))
        if found is not None:
            factor, approx = found
            return Converted(base * factor / to_unit.base_factor, approx)
    return None


def format_qty(amount: float, unit_abbr: str | None) -> str:
    """Finnish display: 3.5 + 'dl' -> '3,5 dl'; 2.0 + None -> '2'."""
    text = f"{float(amount):.2f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    text = text.replace(".", ",")
    return f"{text} {unit_abbr}" if unit_abbr else text
