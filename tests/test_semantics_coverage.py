"""Coverage + provenance checks for the description-driven mapper.

Two guarantees:

1. Live-wire coverage: every capability property the panels actually publish (as
   captured in the tree fixtures) has a ``SEMANTICS`` entry, so a renamed or
   added wire property is a loud failure, not a silently dropped entity. The
   live wire is the coverage oracle because SPAN publishes many properties
   beyond the spec catalogs (the whole ``status`` node, for one).

2. Vendor provenance: the pinned versions in ``.ebus-spec.json`` match the
   versions of the vendored catalogs under ``spec/`` — so a botched or partial
   re-vendor (``scripts/sync_spec.py``) is caught here rather than shipping a
   lockfile that lies about what is vendored.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.span_ebus.semantics import SEMANTICS

REPO = Path(__file__).parent.parent
SPEC = REPO / "custom_components" / "span_ebus" / "spec"
FIXTURES = REPO / "tests" / "fixtures" / "tree"
LOCKFILE = REPO / ".ebus-spec.json"


def _fixture_wire_properties(name: str) -> set[tuple[str, str, str]]:
    """Return every (device_class, capability, property) a fixture declares."""
    devices = json.loads((FIXTURES / name).read_text())["devices"]
    seen: set[tuple[str, str, str]] = set()
    for dev in devices.values():
        desc = dev.get("description") or {}
        device_class = (desc.get("type") or "").removeprefix("energy.ebus.device.")
        for capability, node in (desc.get("nodes") or {}).items():
            for prop_id in (node.get("properties") or {}):
                seen.add((device_class, capability, prop_id))
    return seen


def test_semantics_rows_are_well_formed() -> None:
    """Every SEMANTICS row carries at least a platform and a name."""
    for key, row in SEMANTICS.items():
        assert "platform" in row, key
        assert row.get("name"), key


def test_live_wire_is_fully_mapped() -> None:
    """No property the lc1 panel publishes is left without a SEMANTICS entry."""
    wire = _fixture_wire_properties("nt-2143-c1akc.json")
    unmapped = sorted(k for k in wire if k not in SEMANTICS)
    assert unmapped == [], f"live-wire properties with no SEMANTICS entry: {unmapped}"


def test_lockfile_versions_match_vendored_catalogs() -> None:
    """.ebus-spec.json ``implements`` versions equal the vendored catalog versions."""
    lock = json.loads(LOCKFILE.read_text())
    implements = lock["implements"]
    for kind, subdir in (("capabilities", "capabilities"), ("devices", "devices")):
        for name, pinned in implements.get(kind, {}).items():
            catalog = json.loads((SPEC / subdir / f"{name}.json").read_text())
            assert catalog["version"] == pinned, (
                f"{kind}/{name}: lockfile pins {pinned} but vendored catalog is {catalog['version']}"
            )


def test_vendored_catalog_set_is_present() -> None:
    """The full catalog set was vendored (guards a partial sync)."""
    assert len(list((SPEC / "capabilities").glob("*.json"))) == 25
    assert len(list((SPEC / "devices").glob("*.json"))) == 7
    assert (SPEC / "schemas" / "property-catalog.schema.json").exists()
    assert (SPEC / "schemas" / "device-profile.schema.json").exists()
