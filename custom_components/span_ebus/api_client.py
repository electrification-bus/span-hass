"""Async REST v2 client for SPAN Panel configuration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
import uuid

import aiohttp

from .const import API_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class SpanApiError(Exception):
    """Base exception for SPAN API errors."""


class SpanAuthError(SpanApiError):
    """Authentication failed."""


class SpanConnectionError(SpanApiError):
    """Connection to panel failed."""


@dataclass
class StatusResponse:
    """Response from GET /api/v2/status."""

    serial_number: str
    firmware_version: str


@dataclass
class AuthResponse:
    """Response from POST /api/v2/auth/register."""

    access_token: str
    serial_number: str
    ebus_broker_username: str
    ebus_broker_password: str
    ebus_broker_host: str
    ebus_broker_mqtts_port: int


class SpanApiClient:
    """Async client for SPAN Panel REST API v2.

    Used only during config flow for authentication and certificate retrieval.
    Runtime data comes via MQTT/Homie (ebus-sdk Controller).
    """

    def __init__(self, host: str, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize the API client."""
        self._host = host
        self._session = session
        self._own_session = session is None

    @property
    def _base_url(self) -> str:
        return f"http://{self._host}"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """Close the client session if we own it."""
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str) -> Any:
        """Make a GET request."""
        session = await self._ensure_session()
        url = f"{self._base_url}{path}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
                if resp.status == 401:
                    raise SpanAuthError("Authentication required")
                resp.raise_for_status()
                content_type = resp.content_type or ""
                if "json" in content_type:
                    return await resp.json()
                return await resp.text()
        except aiohttp.ClientConnectorError as err:
            raise SpanConnectionError(f"Cannot connect to {self._host}") from err
        except aiohttp.ClientResponseError as err:
            raise SpanApiError(f"API error: {err.status} {err.message}") from err

    async def _post(self, path: str, json_data: dict | None = None) -> Any:
        """Make a POST request."""
        session = await self._ensure_session()
        url = f"{self._base_url}{path}"
        try:
            async with session.post(
                url,
                json=json_data,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise SpanAuthError("Invalid passphrase")
                if resp.status == 403:
                    raise SpanAuthError(
                        "Registration denied. Ensure door bypass is active or passphrase is correct."
                    )
                if resp.status == 422:
                    detail = ""
                    try:
                        body = await resp.json()
                        detail = body.get("detail", "")
                    except Exception:
                        pass
                    raise SpanAuthError(detail or "Authentication rejected")
                resp.raise_for_status()
                return await resp.json()
        except (SpanAuthError, SpanConnectionError):
            raise
        except aiohttp.ClientConnectorError as err:
            raise SpanConnectionError(f"Cannot connect to {self._host}") from err
        except aiohttp.ClientResponseError as err:
            raise SpanApiError(f"API error: {err.status} {err.message}") from err

    async def get_status(self) -> StatusResponse:
        """Get panel serial number and firmware version.

        GET /api/v2/status — no authentication required.
        """
        data = await self._get("/api/v2/status")
        return StatusResponse(
            serial_number=data["serialNumber"],
            firmware_version=data["firmwareVersion"],
        )

    async def register(
        self,
        passphrase: str | None = None,
    ) -> AuthResponse:
        """Register client and obtain access token + MQTT credentials.

        POST /api/v2/auth/register
        The `name` field must be unique per panel — include a random suffix.
        With passphrase: include hopPassphrase in body.
        Without passphrase (door bypass): omit hopPassphrase.
        """
        suffix = uuid.uuid4().hex[:8]
        json_data: dict[str, str] = {"name": f"home-assistant-{suffix}"}
        if passphrase:
            json_data["hopPassphrase"] = passphrase

        data = await self._post("/api/v2/auth/register", json_data)

        return AuthResponse(
            access_token=data["accessToken"],
            serial_number=data["serialNumber"],
            ebus_broker_username=data["ebusBrokerUsername"],
            ebus_broker_password=data["ebusBrokerPassword"],
            ebus_broker_host=data["ebusBrokerHost"],
            ebus_broker_mqtts_port=data["ebusBrokerMqttsPort"],
        )

    async def get_ca_certificate(self) -> str:
        """Download the panel's CA certificate in PEM format.

        GET /api/v2/certificate/ca — no authentication required.
        """
        return await self._get("/api/v2/certificate/ca")
