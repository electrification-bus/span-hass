"""Coverage + provenance checks for the description-driven mapper.

The coverage ORACLE is the adapter's own generated schema
(``GET /api/v2/homie/schema``), vendored as
``tests/fixtures/adapter-homie-schema.json``. It enumerates every device class /
capability / property the SPAN ebus-panel-adapter can publish, including ones
not instantiated on the reference panels (EVSE, or the forward-declared ``doe``
capability). Tracking the adapter schema is the adapter-first guarantee: it is
neither the upstream spec (aspirational: device profiles list capabilities the
adapter does not publish) nor only the live wire (which shows only the devices a
given panel happens to have). When the adapter adds or renames a property,
``SEMANTICS`` must gain an entry or ``test_adapter_schema_is_fully_mapped`` fails.

Refresh the vendored schema from a panel with:
    curl http://<panel-host>/api/v2/homie/schema > tests/fixtures/adapter-homie-schema.json
(unauthenticated; returns type definitions only, no device values or serials.)

Provenance is also checked: the pinned versions in ``.ebus-spec.json`` must match
the vendored spec catalogs under ``spec/``, so a botched re-vendor is caught here.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.span_ebus.semantics import SEMANTICS

REPO = Path(__file__).parent.parent
SPEC = REPO / "custom_components" / "span_ebus" / "spec"
FIXTURES = REPO / "tests" / "fixtures"
LOCKFILE = REPO / ".ebus-spec.json"


def _schema_properties() -> set[tuple[str, str, str]]:
    """Every (device_class, capability, property) the adapter schema declares."""
    schema = json.loads((FIXTURES / "adapter-homie-schema.json").read_text())
    return {
        (device_class, capability, prop)
        for device_class, caps in schema["deviceClasses"].items()
        for capability, props in caps.items()
        for prop in props
    }


def _fixture_wire_properties(name: str) -> set[tuple[str, str, str]]:
    """Every (device_class, capability, property) a tree fixture declares."""
    devices = json.loads((FIXTURES / "tree" / name).read_text())["devices"]
    seen: set[tuple[str, str, str]] = set()
    for dev in devices.values():
        desc = dev.get("description") or {}
        device_class = (desc.get("type") or "").removeprefix("energy.ebus.device.")
        for capability, node in (desc.get("nodes") or {}).items():
            for prop in (node.get("properties") or {}):
                seen.add((device_class, capability, prop))
    return seen


def test_semantics_rows_are_well_formed() -> None:
    """Every SEMANTICS row carries at least a platform and a name."""
    for key, row in SEMANTICS.items():
        assert "platform" in row, key
        assert row.get("name"), key


def test_adapter_schema_is_fully_mapped() -> None:
    """Every property the adapter can publish has a SEMANTICS entry (no silent drops)."""
    unmapped = sorted(k for k in _schema_properties() if k not in SEMANTICS)
    assert unmapped == [], f"adapter-schema properties with no SEMANTICS entry: {unmapped}"


def test_live_fixture_is_a_subset_of_the_adapter_schema() -> None:
    """The captured panel fixture only carries properties the schema declares.

    Catches a stale fixture (or a schema that has moved on) before it can mask a
    coverage gap.
    """
    schema = _schema_properties()
    stray = sorted(k for k in _fixture_wire_properties("nt-2143-c1akc.json") if k not in schema)
    assert stray == [], f"fixture properties absent from the adapter schema: {stray}"


def test_lockfile_versions_match_vendored_catalogs() -> None:
    """.ebus-spec.json ``implements`` versions equal the vendored catalog versions."""
    lock = json.loads(LOCKFILE.read_text())
    for kind in ("capabilities", "devices"):
        for name, pinned in lock["implements"].get(kind, {}).items():
            catalog = json.loads((SPEC / kind / f"{name}.json").read_text())
            assert catalog["version"] == pinned, (
                f"{kind}/{name}: lockfile pins {pinned} but vendored catalog is {catalog['version']}"
            )


def test_vendored_catalog_set_is_present() -> None:
    """The full catalog set was vendored (guards a partial sync)."""
    assert len(list((SPEC / "capabilities").glob("*.json"))) == 25
    assert len(list((SPEC / "devices").glob("*.json"))) == 7
    assert (SPEC / "schemas" / "property-catalog.schema.json").exists()
    assert (SPEC / "schemas" / "device-profile.schema.json").exists()
