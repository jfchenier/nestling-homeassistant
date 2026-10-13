"""Diagnostics: the family and summaries Home Assistant sees, without the token."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import NestlingConfigEntry

TO_REDACT = {CONF_TOKEN, "email", "book", "book_photos"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NestlingConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data.data
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "family": data.family if data else None,
            "summaries": data.summaries if data else None,
        },
        TO_REDACT,
    )
