"""A gated change acts only on a confirmed call that follows a preview."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "confirm_under_test", Path(__file__).resolve().parents[1] / "confirm.py"
)
confirm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(confirm)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(confirm, "time", SimpleNamespace(monotonic=lambda: now[0]))
    confirm._asked.clear()
    confirm._approved.clear()
    return now


def test_preview_then_yes_acts_once(clock):
    assert confirm.check("k", False, "s")["done"] is False
    clock[0] += 5
    assert confirm.check("k", True, "s") is None
    clock[0] += 60
    assert confirm.check("k", True, "s") is not None  # used up: a later call needs a new yes


def test_confirmed_without_preview_or_too_fast_is_refused(clock):
    assert confirm.check("k", True, "s") is not None  # no preview: this becomes the preview
    clock[0] += 1
    assert confirm.check("k", True, "s") is not None  # chained straight after it
    clock[0] += 3
    assert confirm.check("k", True, "s") is None  # the too-fast try did not restart the wait


def test_replay_of_an_approved_call_passes_on_to_once(clock):
    confirm.check("k", False, "s")
    clock[0] += 5
    assert confirm.check("k", True, "s") is None
    clock[0] += 2
    assert confirm.check("k", True, "s") is None


def test_different_arguments_need_their_own_yes(clock):
    confirm.check("chore Vacuum Thomas", False, "s")
    clock[0] += 5
    assert confirm.check("chore Vacuum Anniina", True, "s") is not None
