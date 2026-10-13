"""Keeps every child's summary up to date: a slow poll, plus the family's live stream."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NestlingAuthError, NestlingClient, NestlingConnectionError, NestlingError
from .const import (
    CONF_FAMILY_ID,
    DOMAIN,
    EVENT_ENTRY_LOGGED,
    REFRESH_DELAY,
    SIGNAL_ENTRY_LOGGED,
    STREAM_BACKOFF_MAX,
    STREAM_SILENCE,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type NestlingConfigEntry = ConfigEntry[NestlingCoordinator]


@dataclass
class NestlingData:
    """The family and a summary (`GET /children/{id}/summary`) per child."""

    family: dict[str, Any]
    summaries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def children(self) -> list[dict[str, Any]]:
        return self.family.get("children", [])


class NestlingCoordinator(DataUpdateCoordinator[NestlingData]):
    """One per config entry (= one family)."""

    config_entry: NestlingConfigEntry

    def __init__(self, hass: HomeAssistant, entry: NestlingConfigEntry, client: NestlingClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.client = client
        self.family_id: str = entry.data[CONF_FAMILY_ID]
        self._stale: set[str] = set()
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._stream_task: asyncio.Task | None = None

    async def _async_update_data(self) -> NestlingData:
        try:
            family = await self.client.family(self.family_id)
            children = family.get("children", [])
            results = await asyncio.gather(*(self.client.summary(c["id"]) for c in children))
        except NestlingAuthError as e:
            raise ConfigEntryAuthFailed(str(e)) from e
        except NestlingError as e:
            raise UpdateFailed(str(e)) from e
        return NestlingData(family=family, summaries={c["id"]: s for c, s in zip(children, results, strict=True)})

    def summary(self, child_id: str) -> dict[str, Any] | None:
        return self.data.summaries.get(child_id) if self.data else None

    async def async_refresh_child(self, child_id: str) -> None:
        """Reload one child's summary now (after an action Home Assistant sent)."""
        if self.data is None or child_id not in self.data.summaries:
            await self.async_request_refresh()
            return
        try:
            summary = await self.client.summary(child_id)
        except NestlingError as e:
            _LOGGER.debug("Refreshing %s failed: %s", child_id, e)
            await self.async_request_refresh()
            return
        self.data.summaries[child_id] = summary
        self.async_set_updated_data(self.data)

    @callback
    def _refresh_later(self, child_id: str | None) -> None:
        """Collect changes for a moment, then reload what they touched (None: everything)."""
        self._stale.add(child_id or "*")
        if self._refresh_handle is None:
            self._refresh_handle = self.hass.loop.call_later(REFRESH_DELAY, lambda: self.hass.async_create_task(self._refresh_stale()))

    async def _refresh_stale(self) -> None:
        self._refresh_handle = None
        stale, self._stale = self._stale, set()
        if "*" in stale or self.data is None or not stale <= self.data.summaries.keys():
            await self.async_request_refresh()
            return
        for child_id in stale:
            await self.async_refresh_child(child_id)

    # Live stream -----------------------------------------------------------------------------

    @callback
    def start_stream(self) -> None:
        self._stream_task = self.config_entry.async_create_background_task(
            self.hass, self._run_stream(), f"{DOMAIN} live stream {self.family_id}"
        )

    async def async_shutdown(self) -> None:
        if self._refresh_handle:
            self._refresh_handle.cancel()
            self._refresh_handle = None
        if self._stream_task:
            self._stream_task.cancel()
        await super().async_shutdown()

    async def _run_stream(self) -> None:
        delay = 1.0
        first = True
        while True:
            try:
                async for event, data in self.client.stream(self.family_id, STREAM_SILENCE):
                    if event == "ready":
                        delay = 1.0
                        # Changes made while disconnected aren't replayed.
                        if not first:
                            await self.async_request_refresh()
                        first = False
                    elif event == "resync":
                        self._refresh_later(None)
                    elif event == "change" and isinstance(data, dict):
                        self._on_change(data)
                _LOGGER.debug("Live stream ended; reconnecting")
            except asyncio.CancelledError:
                raise
            except NestlingAuthError:
                _LOGGER.warning("Nestling refused the token on the live stream")
                self.config_entry.async_start_reauth(self.hass)
                return
            except NestlingConnectionError as e:
                _LOGGER.debug("Live stream lost: %s", e)
            except Exception:
                _LOGGER.exception("Live stream failed")
            first = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, STREAM_BACKOFF_MAX)

    @callback
    def _on_change(self, change: dict[str, Any]) -> None:
        entity, action, data = change.get("entity"), change.get("action"), change.get("data") or {}
        if entity in ("event", "timer"):
            self._refresh_later(data.get("child_id"))
            if entity == "event" and action == "created" and data.get("child_id"):
                self._entry_logged(data)
        else:
            # child, family, import, sync: reload everything.
            self._refresh_later(None)

    @callback
    def _entry_logged(self, event: dict[str, Any]) -> None:
        child = next((c for c in (self.data.children if self.data else []) if c["id"] == event["child_id"]), None)
        self.hass.bus.async_fire(
            EVENT_ENTRY_LOGGED,
            {
                "config_entry_id": self.config_entry.entry_id,
                "child_id": event["child_id"],
                "child": child["name"] if child else None,
                "type": event.get("type"),
                "entry": event,
            },
        )
        async_dispatcher_send(self.hass, SIGNAL_ENTRY_LOGGED.format(self.config_entry.entry_id), event)
