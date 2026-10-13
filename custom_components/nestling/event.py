"""An event entity that fires for every new entry, from Home Assistant or a phone."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_TYPES, SIGNAL_ENTRY_LOGGED
from .coordinator import NestlingConfigEntry
from .entity import NestlingEntity, add_child_entities

KEYS = ("method", "amount_ml", "wet", "dirty", "dry", "potty", "kind", "name", "start", "end", "note")


async def async_setup_entry(hass: HomeAssistant, entry: NestlingConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    add_child_entities(coordinator, async_add_entities, lambda c: [EntryLogged(coordinator, c, "entry_logged")])


class EntryLogged(NestlingEntity, EventEntity):
    _attr_event_types = list(EVENT_TYPES)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ENTRY_LOGGED.format(self.coordinator.config_entry.entry_id), self._logged)
        )

    @callback
    def _logged(self, entry: dict[str, Any]) -> None:
        if entry.get("child_id") != self.child_id or entry.get("type") not in EVENT_TYPES:
            return
        self._trigger_event(entry["type"], {k: entry[k] for k in KEYS if entry.get(k) is not None})
        self.async_write_ha_state()
