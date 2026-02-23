"""Tests for the SPAN Panel (eBus) integration __init__ module."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from custom_components.span_ebus.__init__ import _wait_for_circuit_names


class FakePanel:
    """Minimal panel mock with controllable property values and callbacks."""

    def __init__(self) -> None:
        self._property_values: dict[tuple[str, str], str] = {}
        self._callbacks: dict[tuple[str, str], list[Callable[[str], None]]] = {}

    def get_property_value(self, node_id: str, property_id: str) -> str | None:
        return self._property_values.get((node_id, property_id))

    def register_property_callback(
        self, node_id: str, property_id: str, cb: Callable[[str], None]
    ) -> Callable[[], None]:
        key = (node_id, property_id)
        self._callbacks.setdefault(key, []).append(cb)

        def unreg() -> None:
            cbs = self._callbacks.get(key)
            if cbs and cb in cbs:
                cbs.remove(cb)

        return unreg

    def simulate_property_arrival(self, node_id: str, property_id: str, value: str) -> None:
        """Simulate an MQTT property value arriving: store it and fire callbacks."""
        self._property_values[(node_id, property_id)] = value
        for cb in list(self._callbacks.get((node_id, property_id), [])):
            cb(value)


@pytest.fixture
def panel():
    return FakePanel()


@pytest.mark.asyncio
async def test_wait_names_already_available(panel):
    """All circuit names available immediately — returns True without delay."""
    panel._property_values[("circuit-1", "name")] = "Kitchen"
    panel._property_values[("circuit-2", "name")] = "Bedroom"

    result = await _wait_for_circuit_names(
        panel, ["circuit-1", "circuit-2"], timeout=1.0
    )
    assert result is True


@pytest.mark.asyncio
async def test_wait_names_timeout(panel):
    """No names arrive — returns False after timeout."""
    result = await _wait_for_circuit_names(
        panel, ["circuit-1", "circuit-2"], timeout=0.3
    )
    assert result is False


@pytest.mark.asyncio
async def test_wait_names_partial_timeout(panel):
    """Only some names arrive — returns False after timeout."""
    panel._property_values[("circuit-1", "name")] = "Kitchen"

    result = await _wait_for_circuit_names(
        panel, ["circuit-1", "circuit-2"], timeout=0.3
    )
    assert result is False


@pytest.mark.asyncio
async def test_wait_names_arrive_via_callback(panel):
    """Names arrive via callback after registration — returns True."""

    async def deliver_names():
        await asyncio.sleep(0.05)
        panel.simulate_property_arrival("circuit-1", "name", "Kitchen")
        panel.simulate_property_arrival("circuit-2", "name", "Bedroom")

    asyncio.create_task(deliver_names())

    result = await _wait_for_circuit_names(
        panel, ["circuit-1", "circuit-2"], timeout=2.0
    )
    assert result is True


@pytest.mark.asyncio
async def test_wait_names_empty_list(panel):
    """Empty circuit list — returns True immediately."""
    result = await _wait_for_circuit_names(panel, [], timeout=1.0)
    assert result is True


@pytest.mark.asyncio
async def test_wait_names_callbacks_cleaned_up(panel):
    """Temporary callbacks are unregistered after completion."""
    panel._property_values[("circuit-1", "name")] = "Kitchen"

    await _wait_for_circuit_names(panel, ["circuit-1"], timeout=1.0)

    # The temporary callback should have been removed
    cbs = panel._callbacks.get(("circuit-1", "name"), [])
    assert len(cbs) == 0


@pytest.mark.asyncio
async def test_wait_names_callbacks_cleaned_up_on_timeout(panel):
    """Temporary callbacks are unregistered even on timeout."""
    await _wait_for_circuit_names(panel, ["circuit-1"], timeout=0.1)

    cbs = panel._callbacks.get(("circuit-1", "name"), [])
    assert len(cbs) == 0
