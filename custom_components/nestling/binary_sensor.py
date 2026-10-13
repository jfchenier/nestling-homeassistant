"""Sleeping, nursing, pumping (a timer runs) and medicine due."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestlingConfigEntry, NestlingCoordinator
from .entity import NestlingEntity, add_child_entities


def _running(kind: str) -> Callable[[dict[str, Any]], bool]:
    return lambda s: any(t.get("kind") == kind and t.get("running") for t in s.get("timers", []))


def _due(s: dict[str, Any]) -> bool:
    return any(m.get("due") for m in s.get("medicines", []))


@dataclass(frozen=True, kw_only=True)
class NestlingBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[dict[str, Any]], bool]


BINARY_SENSORS = (
    NestlingBinaryDescription(key="sleeping", is_on_fn=_running("sleep")),
    NestlingBinaryDescription(key="nursing", is_on_fn=_running("breastfeed")),
    NestlingBinaryDescription(key="pumping", is_on_fn=_running("pump")),
    NestlingBinaryDescription(key="medicine_due", is_on_fn=_due),
)


async def async_setup_entry(hass: HomeAssistant, entry: NestlingConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    add_child_entities(coordinator, async_add_entities, lambda c: [NestlingBinarySensor(coordinator, c, d) for d in BINARY_SENSORS])


class NestlingBinarySensor(NestlingEntity, BinarySensorEntity):
    entity_description: NestlingBinaryDescription

    def __init__(self, coordinator: NestlingCoordinator, child: dict[str, Any], description: NestlingBinaryDescription) -> None:
        super().__init__(coordinator, child, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.summary)
