"""Shared base for the entities of one child (one device per child)."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NestlingCoordinator


class NestlingEntity(CoordinatorEntity[NestlingCoordinator]):
    """An entity about one child."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NestlingCoordinator, child: dict[str, Any], key: str) -> None:
        super().__init__(coordinator)
        self.child_id: str = child["id"]
        self._attr_unique_id = f"{self.child_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.child_id)},
            name=child["name"],
            manufacturer="Nestling",
            model="Child",
            configuration_url=coordinator.client.url,
        )

    @property
    def summary(self) -> dict[str, Any]:
        return self.coordinator.summary(self.child_id) or {}

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.summary(self.child_id) is not None

    def timer(self, kind: str) -> dict[str, Any] | None:
        return next((t for t in self.summary.get("timers", []) if t.get("kind") == kind), None)


def add_child_entities(coordinator: NestlingCoordinator, async_add_entities, make) -> None:
    """Add `make(child)` for every child now, and for children added later."""
    known: set[str] = set()

    def add() -> None:
        new = [c for c in (coordinator.data.children if coordinator.data else []) if c["id"] not in known]
        if new:
            known.update(c["id"] for c in new)
            async_add_entities([e for c in new for e in make(c)])

    add()
    coordinator.config_entry.async_on_unload(coordinator.async_add_listener(add))
