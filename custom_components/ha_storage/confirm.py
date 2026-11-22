"""A change made by voice needs the user's yes, enforced here, not in a prompt.

Some assistants never see HA's prompt (Luna's MCP client only lists and calls
tools), so asking first can't be left to instructions. The first call of a
gated tool does nothing and hands back what to read to the user. Only a second
call with confirmed=true, made after a spoken question and answer could have
happened, acts.
"""

from __future__ import annotations

import time

# A read-back and a spoken yes take longer than this; an assistant that chains
# the confirmed call straight after the preview is refused.
MIN_WAIT = 3
MAX_WAIT = 300
# A confirmed call replayed this soon after it was let through goes on to
# once.py, which answers it with the first result instead of acting twice.
REPLAY = 10

DESCRIPTION = (
    "Changes need the user's yes: call it first without confirmed, which does"
    " nothing and returns what to read back; after the user says yes, call it"
    " again with exactly the same arguments and confirmed=true."
)
FIELD = "true only after the user said yes to your read-back of this exact call."

_asked: dict[str, float] = {}
_approved: dict[str, float] = {}


def check(key: str, confirmed: bool, summary: str) -> dict | None:
    """None when the call may act; otherwise the answer to give instead."""
    now = time.monotonic()
    for store in (_asked, _approved):
        for old in [k for k, t in store.items() if now - t > MAX_WAIT]:
            del store[old]
    if confirmed and now - _approved.get(key, float("-inf")) < REPLAY:
        return None
    asked = _asked.get(key)
    if confirmed and asked is not None and now - asked >= MIN_WAIT:
        del _asked[key]
        _approved[key] = now
        return None
    if asked is None:
        _asked[key] = now
    return {
        "done": False,
        "ask_user": (
            f"Nothing was done yet. Ask the user to confirm: {summary}. When they"
            " say yes, call this tool again with exactly the same arguments and"
            " confirmed=true. If they correct you, call it with the corrected"
            " arguments and no confirmed, and ask again."
        ),
    }


def summarize(action: str, args: dict) -> str:
    """'complete chore: chore Vacuum a bit, completed by Thomas'."""
    fields = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in args.items() if v not in (None, ""))
    return f"{action.replace('_', ' ')}: {fields}"
