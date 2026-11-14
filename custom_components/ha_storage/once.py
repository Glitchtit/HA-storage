"""Act once when a voice client sends the same call twice in a burst.

Kiosk Satellite's Gemini Live conversation replays tool calls while earlier
ones are still being answered: one "I took a Red Bull" consumed two.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

# shortcut: an identical call within 10 s counts as a replay, also when the user
# really did repeat themselves that fast; key on a conversation id if that bites.
WINDOW = 10

_recent: dict[str, tuple[float, asyncio.Future]] = {}


async def once(key: str, call: Callable[[], Awaitable]):
    """Run `call` for `key`, or hand back the run a replay of `key` already started."""
    now = time.monotonic()
    for old in [k for k, (started, _) in _recent.items() if now - started > WINDOW]:
        del _recent[old]
    if key not in _recent:
        _recent[key] = (now, asyncio.ensure_future(call()))
    return await asyncio.shield(_recent[key][1])
