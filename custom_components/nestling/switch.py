"""Timer switches: on starts the timer, off stops it and saves the entry."""

from __future__ import annotations

from typing import Any
import uuid

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import NestlingError
from .const import DOMAIN, TIMER_KINDS
from .coordinator import NestlingConfigEntry, NestlingCoordinator
from .entity import NestlingEntity, add_child_entities


async def async_setup_entry(hass: HomeAssistant, entry: NestlingConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    add_child_entities(coordinator, async_add_entities, lambda c: [TimerSwitch(coordinator, c, k) for k in TIMER_KINDS])


class TimerSwitch(NestlingEntity, SwitchEntity):
    """On while the child's timer of this kind runs (or is paused), whoever started it."""

    def __init__(self, coordinator: NestlingCoordinator, child: dict[str, Any], kind: str) -> None:
        super().__init__(coordinator, child, f"{kind}_timer")
        self.kind = kind

    @property
    def is_on(self) -> bool:
        return self.timer(self.kind) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        timer = self.timer(self.kind)
        if timer is None:
            return None
        return {
            "started_at": timer.get("started_at"),
            "running": timer.get("running"),
            "side": timer.get("side"),
            "elapsed_seconds": timer.get("elapsed_seconds"),
        }

    def _start_side(self) -> str | None:
        if self.kind != "breastfeed":
            return None
        entity_id = er.async_get(self.hass).async_get_entity_id("select", DOMAIN, f"{self.child_id}_breastfeed_side")
        state = self.hass.states.get(entity_id) if entity_id else None
        return state.state if state and state.state in ("left", "right") else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        body = {"id": str(uuid.uuid4()), "kind": self.kind}
        if side := self._start_side():
            body["side"] = side
        try:
            await self.coordinator.client.start_timer(self.child_id, body)
        except NestlingError as e:
            raise HomeAssistantError(f"Nestling: {e}") from e
        await self.coordinator.async_refresh_child(self.child_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        timer = self.timer(self.kind)
        if timer is None:
            return
        try:
            await self.coordinator.client.timer_action(timer["id"], "stop", {"event_id": str(uuid.uuid4())})
        except NestlingError as e:
            # 404: another phone stopped it a moment ago; the refresh shows that.
            if e.status != 404:
                raise HomeAssistantError(f"Nestling: {e}") from e
        await self.coordinator.async_refresh_child(self.child_id)
