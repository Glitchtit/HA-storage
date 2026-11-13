"""Hands Storage's actions to voice assistants as Assist LLM tools.

Home Assistant (2026.11) asks every integration's llm.py for tools; without
this file an assistant sees no Storage action at all and treats "consume the
fries" as small talk.
"""

from __future__ import annotations

from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.llm import LLM_API_ASSIST, ActionTool, LLMContext

from .const import DOMAIN

# The shopping list is not here: it is the todo entity, which Assist already drives.
ACTIONS = ("find_stock", "consume_stock")

PROMPT = (
    "The household's food and supplies are tracked in Storage. When the user says"
    " something was eaten, used up, finished, consumed or thrown away, or asks"
    " what there is at home, that is a Storage instruction and never something"
    " for you to eat: look the item up with ha_storage__find_stock, then take it"
    " out of stock with ha_storage__consume_stock."
)


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """Return Storage's tools for the Assist API."""
    if api_id != LLM_API_ASSIST:
        return None
    tools = [
        ActionTool(hass, DOMAIN, action)
        for action in ACTIONS
        if hass.services.has_service(DOMAIN, action)
    ]
    return LLMTools(tools=tools, prompt=PROMPT) if tools else None
