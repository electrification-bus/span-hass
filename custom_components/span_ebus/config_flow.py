"""Config flow for SPAN Panel (eBus) integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import voluptuous as vol

from .api_client import (
    SpanApiClient,
    SpanAuthError,
    SpanConnectionError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CA_CERT_PEM,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_SERIAL_NUMBER,
    DEFAULT_EBUS_BROKER_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SpanEbusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SPAN Panel (eBus)."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str = ""
        self._serial_number: str = ""
        self._firmware_version: str = ""
        self._client: SpanApiClient | None = None

    async def _get_client(self) -> SpanApiClient:
        """Get or create the API client."""
        if self._client is None:
            self._client = SpanApiClient(self._host)
        return self._client

    async def _close_client(self) -> None:
        """Close the API client."""
        if self._client:
            await self._client.close()
            self._client = None

    async def _fetch_status(self) -> dict[str, str] | None:
        """Fetch panel status, returning errors dict or None on success."""
        client = await self._get_client()
        try:
            status = await client.get_status()
        except SpanConnectionError:
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error fetching status")
            return {"base": "unknown"}

        self._serial_number = status.serial_number
        self._firmware_version = status.firmware_version

        await self.async_set_unique_id(self._serial_number)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual host entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            if err := await self._fetch_status():
                errors = err
            else:
                return await self.async_step_auth_menu()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle zeroconf discovery."""
        self._host = str(discovery_info.host)

        # Extract serial from mDNS instance name.
        # Names are like "span-nt-XXXX-XXXXX-EBUS" or "span-nt-XXXX-XXXXX-MQTTS".
        instance = discovery_info.name.split("._")[0]
        _LOGGER.debug("Zeroconf discovery: name=%s, host=%s", discovery_info.name, discovery_info.host)
        if instance.startswith("span-"):
            # Strip the trailing service suffix (-EBUS, -MQTTS, etc.)
            serial_part = instance[len("span-"):]
            # Remove last segment after final hyphen (the suffix)
            self._serial_number = serial_part.rsplit("-", 1)[0]

        # Set unique ID early to deduplicate (each panel advertises on
        # both _ebus._tcp and _secure-mqtt._tcp).
        if self._serial_number:
            await self.async_set_unique_id(self._serial_number)
            self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})

        # Set title_placeholders early so the discovery card shows the serial.
        self.context["title_placeholders"] = {
            "serial": self._serial_number,
        }

        if err := await self._fetch_status():
            return self.async_abort(reason="cannot_connect")

        return await self.async_step_auth_menu()

    async def async_step_auth_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show auth method menu."""
        return self.async_show_menu(
            step_id="auth_menu",
            menu_options=["auth_passphrase", "auth_door_bypass"],
            description_placeholders={
                "serial": self._serial_number,
                "firmware": self._firmware_version,
            },
        )

    async def async_step_auth_passphrase(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle passphrase authentication."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = await self._get_client()
            try:
                auth = await client.register(passphrase=user_input["passphrase"])
            except SpanAuthError:
                errors["base"] = "invalid_auth"
            except SpanConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during auth")
                errors["base"] = "unknown"
            else:
                return await self._async_finish_auth(auth)

        return self.async_show_form(
            step_id="auth_passphrase",
            data_schema=vol.Schema({vol.Required("passphrase"): str}),
            errors=errors,
        )

    async def async_step_auth_door_bypass(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle door bypass authentication.

        User presses the door switch 3 times, then submits the form.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            client = await self._get_client()
            try:
                auth = await client.register(passphrase=None)
            except SpanAuthError:
                errors["base"] = "door_bypass_not_active"
            except SpanConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during door bypass auth")
                errors["base"] = "unknown"
            else:
                return await self._async_finish_auth(auth)

        return self.async_show_form(
            step_id="auth_door_bypass",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"serial": self._serial_number},
        )

    async def _async_finish_auth(self, auth: Any) -> ConfigFlowResult:
        """Download CA cert and create config entry."""
        client = await self._get_client()
        try:
            ca_cert = await client.get_ca_certificate()
        except Exception:
            _LOGGER.exception("Failed to download CA certificate")
            ca_cert = ""
        finally:
            await self._close_client()

        return self.async_create_entry(
            title=f"SPAN Panel {self._serial_number}",
            data={
                CONF_HOST: self._host,
                CONF_SERIAL_NUMBER: self._serial_number,
                CONF_ACCESS_TOKEN: auth.access_token,
                CONF_EBUS_BROKER_USERNAME: auth.ebus_broker_username,
                CONF_EBUS_BROKER_PASSWORD: auth.ebus_broker_password,
                CONF_EBUS_BROKER_HOST: auth.ebus_broker_host,
                CONF_EBUS_BROKER_PORT: auth.ebus_broker_mqtts_port
                or DEFAULT_EBUS_BROKER_PORT,
                CONF_CA_CERT_PEM: ca_cert,
            },
        )
