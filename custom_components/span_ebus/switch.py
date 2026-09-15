"""Switch platform for SPAN Panel (eBus) integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAPABILITY_SWITCH, DOMAIN
from .entity_base import SpanEbusEntity, async_setup_platform_entities
from .node_mappers import EntitySpec

_LOGGER = logging.getLogger(__name__)

# The relay property and the companion capability that says whether the panel
# will honor a command on it. Per the eBus switch catalog, relay-controllable is
# "True = the relay can be opened and closed by command or automatic shed.
# False = locked (for example a circuit commissioned as permanently on)."
PROPERTY_RELAY = "relay"
PROPERTY_RELAY_CONTROLLABLE = "relay-controllable"

_TRUE_VALUES = {"true", "1", "on", "yes"}
_FALSE_VALUES = {"false", "0", "off", "no"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SPAN switch entities from a config entry."""
    async_setup_platform_entities(
        hass, entry, Platform.SWITCH, async_add_entities, SpanEbusSwitch
    )


class SpanEbusSwitch(SpanEbusEntity, SwitchEntity):
    """A switch entity for a SPAN circuit relay.

    A circuit relay is only operable when the panel says so: commissioning can
    lock a circuit permanently on or permanently off, and the panel reports that
    per circuit on ``switch/relay-controllable``, republishing it when the
    circuit is re-commissioned. The gate is therefore read live on every write
    rather than frozen into the entity when it is built, and the entity is
    created either way, because a locked relay still reports a real CLOSED or
    OPEN state and a circuit that becomes operable later has to become operable
    without a config-entry reload.
    """

    def __init__(self, panel: Any, spec: EntitySpec) -> None:
        """Initialize the switch."""
        super().__init__(panel=panel, spec=spec)
        self._spec = spec
        if spec.icon:
            self._attr_icon = spec.icon

    @property
    def _is_relay(self) -> bool:
        """Whether this switch drives a circuit relay.

        The circuit relay is the only switch the panels publish today, but the
        platform stays general: the non-relay branches keep a future settable
        boolean from silently inheriting the relay's write gate.
        """
        return (
            self._capability == CAPABILITY_SWITCH
            and self._property_id == PROPERTY_RELAY
        )

    def _write_allowed(self) -> bool:
        """Whether the panel currently accepts a command on this property.

        Three cases, in order. A reported ``relay-controllable`` value is the
        panel's own live answer and wins outright. A publisher that declares the
        capability but has not yet delivered its value gets a refusal, not a
        fallback: the fallback is the relay's build-time ``$settable``, and the
        adapter's class-level schema declares the relay settable unconditionally,
        so falling through there would let a command out on a locked circuit
        during the window before the retained value lands. Only a publisher that
        does not implement the capability at all (it is SHOULD-level in the spec)
        falls back to ``$settable``.
        """
        if not self._is_relay:
            return True

        raw = self._panel.get_property_value(
            self._device_id, CAPABILITY_SWITCH, PROPERTY_RELAY_CONTROLLABLE
        )
        if raw is not None:
            token = str(raw).strip().lower()
            if token in _TRUE_VALUES:
                return True
            if token in _FALSE_VALUES:
                return False

        if self._panel.is_property_declared(
            self._device_id, CAPABILITY_SWITCH, PROPERTY_RELAY_CONTROLLABLE
        ):
            return False

        return self._spec.settable

    def _assert_write_allowed(self) -> None:
        """Refuse a command the panel has told us it will not honor."""
        if self._write_allowed():
            return
        _LOGGER.debug(
            "Refusing relay command for %s: panel does not report this circuit "
            "as controllable",
            self.entity_id or self._attr_unique_id,
        )
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="relay_not_controllable",
            translation_placeholders={
                "entity_id": self.entity_id or str(self._attr_unique_id)
            },
        )

    def _update_from_value(self, value: str) -> None:
        """Map publisher's enum / boolean state to HA's on/off."""
        # Circuit relay: CLOSED=on, OPEN=off. Any other boolean: true=on.
        upper = value.upper()
        if upper in {"CLOSED", "TRUE", "ON", "1", "YES"}:
            self._attr_is_on = True
        elif upper in {"OPEN", "FALSE", "OFF", "0", "NO"}:
            self._attr_is_on = False
        else:
            self._attr_is_on = None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send the on-side value the publisher expects."""
        self._assert_write_allowed()
        # The relay takes CLOSED/OPEN; any other settable boolean takes true/false.
        payload = "CLOSED" if self._property_id == PROPERTY_RELAY else "true"
        self._panel.set_property(
            self._device_id, self._capability, self._property_id, payload
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send the off-side value the publisher expects."""
        self._assert_write_allowed()
        payload = "OPEN" if self._property_id == PROPERTY_RELAY else "false"
        self._panel.set_property(
            self._device_id, self._capability, self._property_id, payload
        )
