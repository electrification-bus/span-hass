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
    """Authentication failed.

    ``detail`` is the panel's own message when its 422 body carries one as a
    string, and None otherwise.
    """

    def __init__(self, message: str, detail: str | None = None) -> None:
        """Initialize with an optional panel-supplied detail message."""
        super().__init__(message)
        self.detail = detail


class SpanConnectionError(SpanApiError):
    """Connection to panel failed."""


def is_pem_certificate(text: str) -> bool:
    """Return True if ``text`` holds a PEM-encoded certificate."""
    return (
        "-----BEGIN CERTIFICATE-----" in text
        and "-----END CERTIFICATE-----" in text
    )
class SpanNotReadyError(SpanApiError):
    """Panel is not ready to register clients yet (HTTP 503); retry shortly."""


@dataclass
class StatusResponse:
    """Response from GET /api/v2/status."""

    serial_number: str
    firmware_version: str
    # Absent on firmware before r202639.
    hardware_version: str | None = None


@dataclass
class AuthResponse:
    """Response from POST /api/v2/auth/register."""

    access_token: str
    serial_number: str
    ebus_broker_username: str
    # None when the panel cannot read its passphrase (r202639 and later).
    ebus_broker_password: str | None
    ebus_broker_host: str
    ebus_broker_mqtts_port: int


class SpanApiClient:
    """Async client for SPAN Panel REST API v2.

    Used by the config flow for authentication and certificate retrieval, and
    at setup to download a CA certificate missing from the entry.
    Runtime data comes via MQTT/Homie (ebus-sdk Controller).
    """

    def __init__(self, host: str, session: aiohttp.ClientSession) -> None:
        """Initialize the API client.

        ``session`` is Home Assistant's shared client session. The client never
        owns it and must never close it.
        """
        self._host = host
        self._session = session

    @property
    def _base_url(self) -> str:
        return f"http://{self._host}"

    async def _get(self, path: str) -> Any:
        """Make a GET request."""
        session = self._session
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
        session = self._session
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
                    detail = await self._error_detail(resp)
                    raise SpanAuthError(detail or "Authentication rejected", detail)
                if resp.status == 503:
                    detail = await self._error_detail(resp)
                    raise SpanNotReadyError(detail or "Panel is not ready yet")
                resp.raise_for_status()
                return await resp.json()
        except SpanApiError:
            raise
        except aiohttp.ClientConnectorError as err:
            raise SpanConnectionError(f"Cannot connect to {self._host}") from err
        except aiohttp.ClientResponseError as err:
            raise SpanApiError(f"API error: {err.status} {err.message}") from err

    @staticmethod
    async def _error_detail(resp: aiohttp.ClientResponse) -> str | None:
        """Return the string ``detail`` of an error body, or None.

        The panel sends ``{"detail": "<message>"}``. A missing or unparsable
        body, or a ``detail`` that is not a non-empty string, yields None.
        """
        try:
            body = await resp.json(content_type=None)
        except Exception:
            return None
        detail = body.get("detail") if isinstance(body, dict) else None
        return detail if isinstance(detail, str) and detail else None

    async def get_status(self) -> StatusResponse:
        """Get panel serial number, firmware version, and hardware version.

        GET /api/v2/status — no authentication required.
        """
        data = await self._get("/api/v2/status")
        return StatusResponse(
            serial_number=data["serialNumber"],
            firmware_version=data["firmwareVersion"],
            hardware_version=data.get("hardwareVersion"),
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
            ebus_broker_password=data.get("ebusBrokerPassword"),
            ebus_broker_host=data["ebusBrokerHost"],
            ebus_broker_mqtts_port=data["ebusBrokerMqttsPort"],
        )

    async def get_ca_certificate(self) -> str:
        """Download the panel's CA certificate in PEM format.

        GET /api/v2/certificate/ca — no authentication required.
        """
        result = await self._get("/api/v2/certificate/ca")
        return str(result) if result is not None else ""
