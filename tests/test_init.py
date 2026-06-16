"""Tests for non-platform helpers in ``__init__.py``."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.span_ebus import _resolve_upstream_panel


def _mock_panel(serial: str, fed_by_id: str | None, fed_by_type: str | None) -> MagicMock:
    """Build a SpanPanel mock that returns the given lugs-up/connection values."""
    panel = MagicMock()
    panel.serial_number = serial

    expected = {
        (f"{serial}-lugs-up", "connection", "fed-by-device-id"): fed_by_id,
        (f"{serial}-lugs-up", "connection", "fed-by-device-type"): fed_by_type,
    }
    panel.get_property_value = lambda *args, **kwargs: expected.get(args)
    return panel


def test_resolve_upstream_panel_returns_serial_for_distribution_enclosure() -> None:
    """G3P-24911 cascade case: this panel's upstream is another panel.

    Matches the example from SPAN-c7h: panel lc2 sees lc1 (a
    distribution-enclosure) as its upstream and should link via_device → lc1.
    """
    panel = _mock_panel(
        serial="nt-2204-lc2",
        fed_by_id="nt-2143-lc1",
        fed_by_type="energy.ebus.device.distribution-enclosure",
    )
    assert _resolve_upstream_panel(panel) == "nt-2143-lc1"


def test_resolve_upstream_panel_returns_none_for_bess() -> None:
    """Top-of-cascade case: this panel sits directly under a BESS.

    The BESS is already a child of this panel via the Homie parent/child
    tree (BESS publishes parent = panel-serial), so setting via_device on
    the panel pointing at the BESS would create a cycle. Return None so
    the panel stays at the top of the HA device hierarchy.
    """
    panel = _mock_panel(
        serial="nt-2143-c1akc",
        fed_by_id="nt-2143-c1akc-tg121153003k7g",
        fed_by_type="energy.ebus.device.bess",
    )
    assert _resolve_upstream_panel(panel) is None


def test_resolve_upstream_panel_returns_none_for_utility_feed() -> None:
    """No upstream pointer published = utility feed.

    Single-panel install with grid feed, or a panel whose firmware predates
    G3P-24911 and doesn't publish the triplet at all.
    """
    panel = _mock_panel(
        serial="nt-2143-c1akc", fed_by_id=None, fed_by_type=None
    )
    assert _resolve_upstream_panel(panel) is None


def test_resolve_upstream_panel_returns_none_when_id_present_but_type_unknown() -> None:
    """Defensive against unknown fed-by-device-type values.

    A fed-by-device-id without a recognised type shouldn't crash or guess.
    Return None so the panel stays unlinked rather than mislink.
    """
    panel = _mock_panel(
        serial="nt-2143-c1akc",
        fed_by_id="some-other-device",
        fed_by_type="io.somevendor.gadget",
    )
    assert _resolve_upstream_panel(panel) is None
