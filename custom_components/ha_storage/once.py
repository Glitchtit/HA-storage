"""Act once when a voice client sends the same call twice in a burst.

Kiosk Satellite's Gemini Live conversation replays tool calls while earlier
ones are still being answered: one "I took a Red Bull" consumed two.
"""

from __future__ import annotations

import asyncio
import dataclasses
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
    result = await asyncio.shield(_recent[key][1])
    # HA's tools return a ToolResult whose data is the dict.
    data = getattr(result, "data", result)
    if not isinstance(data, dict):
        return result
    # Tell the assistant, so a real second "I took one" is not swallowed unheard.
    noted = {**data, "repeat": REPEAT_NOTE}
    return noted if data is result else dataclasses.replace(result, data=noted)


REPEAT_NOTE = (
    f"This exact call was made less than {WINDOW} seconds ago and was not done"
    " again. If the user really meant it twice, say so and repeat it after a"
    " moment."
)
