"""A voice-assistant call that arrives twice in a burst acts once."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "once_under_test", Path(__file__).resolve().parents[1] / "once.py"
)
once_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(once_mod)


def test_a_replayed_call_runs_once_and_both_callers_get_the_result():
    runs = []

    async def consume():
        runs.append(1)
        await asyncio.sleep(0.01)
        return {"consumed": 1}

    async def burst():
        return await asyncio.gather(
            once_mod.once("consume 728", consume), once_mod.once("consume 728", consume)
        )

    assert asyncio.run(burst()) == [{"consumed": 1}, {"consumed": 1}]
    assert runs == [1]


def test_the_same_call_acts_again_once_the_window_has_passed(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(once_mod, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    runs = []

    async def consume():
        runs.append(1)
        return len(runs)

    async def two_requests():
        first = await once_mod.once("consume 9", consume)
        clock[0] += once_mod.WINDOW + 1
        return first, await once_mod.once("consume 9", consume)

    assert asyncio.run(two_requests()) == (1, 2)
