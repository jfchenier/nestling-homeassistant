"""A small async client for the Nestling server's API (docs/API.md in the Nestling repository)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_PATH = "/api/v1"


class NestlingError(Exception):
    """The server refused a request; the message is the server's own."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class NestlingAuthError(NestlingError):
    """The token is missing, revoked, or (sign-in) the email or password is wrong."""


class NestlingConnectionError(NestlingError):
    """The server can't be reached."""


def base_url(url: str) -> str:
    """`https://nestling.example.com/` or `.../api/v1` → `https://nestling.example.com`."""
    url = url.strip().rstrip("/")
    if url.endswith(API_PATH):
        url = url[: -len(API_PATH)]
    return url


class NestlingClient:
    """Talks to one Nestling server with one token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str | None = None) -> None:
        self._session = session
        self.url = base_url(url)
        self.token = token

    def _headers(self, token: str | None = None) -> dict[str, str]:
        token = token or self.token
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        params: dict[str, str] | None = None,
        token: str | None = None,
    ) -> Any:
        """Send one request; returns the JSON answer (None for 204)."""
        try:
            async with self._session.request(
                method,
                f"{self.url}{API_PATH}{path}",
                json=body,
                params=params,
                headers=self._headers(token),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as res:
                if res.status == 204:
                    return None
                try:
                    data = await res.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    data = None
                if res.status < 400:
                    return data
                err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
                message = err.get("message") or f"HTTP {res.status}"
                if res.status == 401:
                    raise NestlingAuthError(message, res.status, err.get("code"))
                raise NestlingError(message, res.status, err.get("code"))
        except (TimeoutError, aiohttp.ClientError) as e:
            raise NestlingConnectionError(f"can't reach {self.url}: {e}") from e

    # Accounts --------------------------------------------------------------------------------

    async def setup_status(self) -> dict[str, Any]:
        """Public: answers on any Nestling server, so it checks the URL."""
        data = await self.request("GET", "/auth/setup")
        if not isinstance(data, dict) or "needs_setup" not in data:
            raise NestlingConnectionError(f"{self.url} doesn't look like a Nestling server")
        return data

    async def login(self, email: str, password: str) -> str:
        """A short session token (signed out again once setup is done)."""
        try:
            data = await self.request("POST", "/auth/login", {"email": email, "password": password})
        except NestlingError as e:
            if e.status == 400:
                raise NestlingAuthError(str(e), e.status, e.code) from e
            raise
        return data["token"]

    async def logout(self, token: str) -> None:
        await self.request("POST", "/auth/logout", token=token)

    async def me(self, token: str | None = None) -> dict[str, Any]:
        return await self.request("GET", "/me", token=token)

    async def create_integration(self, family_id: str, name: str, token: str) -> dict[str, Any]:
        """Add a connected app to the family (owners); the answer carries its token."""
        return await self.request("POST", f"/families/{family_id}/integrations", {"name": name}, token=token)

    # Family data -----------------------------------------------------------------------------

    async def family(self, family_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/families/{family_id}")

    async def summary(self, child_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/children/{child_id}/summary")

    async def events(self, child_id: str, types: list[str] | None = None, limit: int = 1) -> list[dict[str, Any]]:
        params = {"limit": str(limit)}
        if types:
            params["type"] = ",".join(types)
        data = await self.request("GET", f"/children/{child_id}/events", params=params)
        return data["events"] if isinstance(data, dict) else data

    async def events_between(self, child_id: str, start: str, end: str, max_pages: int = 10) -> list[dict[str, Any]]:
        """Entries starting in [start, end), newest first, following the pages."""
        found: list[dict[str, Any]] = []
        to = end
        for _ in range(max_pages):
            data = await self.request("GET", f"/children/{child_id}/events", params={"from": start, "to": to, "limit": "500"})
            found.extend(data["events"])
            if not (to := data.get("next_to")):
                break
        return found

    async def trends(self, child_id: str, days: int = 7) -> dict[str, Any]:
        return await self.request("GET", f"/children/{child_id}/trends", params={"days": str(days)})

    # Logging ---------------------------------------------------------------------------------

    async def create_event(self, child_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/children/{child_id}/events", body)

    # Timers ----------------------------------------------------------------------------------

    async def timers(self, child_id: str) -> list[dict[str, Any]]:
        data = await self.request("GET", f"/children/{child_id}/timers")
        return data["timers"] if isinstance(data, dict) else data

    async def start_timer(self, child_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/children/{child_id}/timers", body)

    async def timer_action(self, timer_id: str, action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """`pause`, `resume`, `switch` or `stop`."""
        return await self.request("POST", f"/timers/{timer_id}/{action}", body)

    async def edit_timer(self, timer_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PATCH", f"/timers/{timer_id}", body)

    async def discard_timer(self, timer_id: str) -> None:
        await self.request("DELETE", f"/timers/{timer_id}")

    async def continue_event(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/events/{event_id}/continue", body)

    # Live stream -----------------------------------------------------------------------------

    async def stream(self, family_id: str, silence: float) -> AsyncIterator[tuple[str, Any]]:
        """Yield `(event, data)` from the family's Server-Sent Events stream until it ends.

        Raises NestlingConnectionError when nothing (not even a ping) came for `silence` seconds.
        """
        try:
            async with self._session.get(
                f"{self.url}{API_PATH}/families/{family_id}/stream",
                headers={**self._headers(), "Accept": "text/event-stream"},
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30),
            ) as res:
                if res.status == 401:
                    raise NestlingAuthError("the token was refused", 401)
                if res.status >= 400:
                    raise NestlingError(f"stream answered HTTP {res.status}", res.status)
                event, data = "message", []
                while True:
                    try:
                        async with asyncio.timeout(silence):
                            raw = await res.content.readline()
                    except TimeoutError as e:
                        raise NestlingConnectionError("the live stream went quiet") from e
                    if not raw:
                        return
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if not line:
                        if data:
                            text = "\n".join(data)
                            try:
                                payload: Any = json.loads(text)
                            except ValueError:
                                payload = text
                            yield event, payload
                        elif event != "message":
                            yield event, None
                        event, data = "message", []
                    elif line.startswith(":"):
                        continue
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
        except (TimeoutError, aiohttp.ClientError) as e:
            raise NestlingConnectionError(f"live stream: {e}") from e
