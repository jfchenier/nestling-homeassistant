"""One-tap diaper buttons."""

from __future__ import annotations

from typing import Any
import uuid

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import NestlingError
from .coordinator import NestlingConfigEntry, NestlingCoordinator
from .entity import NestlingEntity, add_child_entities

DIAPERS: dict[str, dict[str, bool]] = {
    "diaper_wet": {"wet": True},
    "diaper_dirty": {"dirty": True},
    "diaper_both": {"wet": True, "dirty": True},
    "diaper_dry": {"dry": True},
}


async def async_setup_entry(hass: HomeAssistant, entry: NestlingConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    add_child_entities(coordinator, async_add_entities, lambda c: [DiaperButton(coordinator, c, k) for k in DIAPERS])


class DiaperButton(NestlingEntity, ButtonEntity):
    """Logs a diaper change now."""

    def __init__(self, coordinator: NestlingCoordinator, child: dict[str, Any], key: str) -> None:
        super().__init__(coordinator, child, key)
        self._diaper = DIAPERS[key]

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.create_event(self.child_id, {"id": str(uuid.uuid4()), "type": "diaper", **self._diaper})
        except NestlingError as e:
            raise HomeAssistantError(f"Nestling: {e}") from e
        await self.coordinator.async_refresh_child(self.child_id)
