"""Hands Storage's actions to voice assistants as Assist LLM tools.

Home Assistant (2026.11) asks every integration's llm.py for tools; without
this file an assistant sees no Storage action at all and treats "consume the
fries" as small talk.
"""

from __future__ import annotations

import json

from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.llm import LLM_API_ASSIST, ActionTool, LLMContext

from .const import DOMAIN
from .once import once

# The shopping list is not here: it is the todo entity, which Assist already drives.
ACTIONS = ("find_stock", "consume_stock", "add_stock")

PROMPT = (
    "The household's food and supplies are tracked in Storage. When the user says"
    " something was eaten, used up, finished, consumed or thrown away, or asks"
    " what there is at home, that is a Storage instruction and never something"
    " for you to eat: look the item up with ha_storage__find_stock, then take it"
    " out of stock with ha_storage__consume_stock (spoiled when it went bad)."
    " Something bought or restocked goes in with ha_storage__add_stock, and"
    " ha_storage__find_stock with expiring_within_days answers what expires soon."
)

# Everything else changes something, and must not do it twice on a replay.
READ_ONLY = ('find_stock',)


class OnceTool(ActionTool):
    """An action that changes something: a replayed call gets the first result."""

    async def async_call(self, hass, tool_input, llm_context):
        key = f"{self.name} {json.dumps(tool_input.tool_args, sort_keys=True, default=str)}"
        return await once(
            key, lambda: ActionTool.async_call(self, hass, tool_input, llm_context)
        )


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """Return Storage's tools for the Assist API."""
    if api_id != LLM_API_ASSIST:
        return None
    tools = [
        (ActionTool if action in READ_ONLY else OnceTool)(hass, DOMAIN, action)
        for action in ACTIONS
        if hass.services.has_service(DOMAIN, action)
    ]
    return LLMTools(tools=tools, prompt=PROMPT) if tools else None
