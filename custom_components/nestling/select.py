"""Breastfeeding side: switches side while nursing, and is the side the next timer starts on."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api import NestlingError
from .coordinator import NestlingConfigEntry
from .entity import NestlingEntity, add_child_entities


async def async_setup_entry(hass: HomeAssistant, entry: NestlingConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    add_child_entities(coordinator, async_add_entities, lambda c: [BreastSide(coordinator, c, "breastfeed_side")])


class BreastSide(NestlingEntity, SelectEntity, RestoreEntity):
    _attr_options = ["left", "right"]
    _next = "left"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in self._attr_options:
            self._next = last.state

    @property
    def current_option(self) -> str:
        timer = self.timer("breastfeed")
        if timer and timer.get("side") in self._attr_options:
            return timer["side"]
        return self._next

    async def async_select_option(self, option: str) -> None:
        timer = self.timer("breastfeed")
        if timer is None:
            self._next = option
            self.async_write_ha_state()
            return
        try:
            await self.coordinator.client.timer_action(timer["id"], "switch", {"side": option})
        except NestlingError as e:
            raise HomeAssistantError(f"Nestling: {e}") from e
        await self.coordinator.async_refresh_child(self.child_id)
