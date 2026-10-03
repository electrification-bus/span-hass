"""Coverage, conformance, and provenance checks for the description-driven mapper.

This repo carries two vendored models and uses both:

* The vendored schemas are the source-of-truth span-hass MODELS: every device
  class / capability / property a SPAN Panel can publish, including ones the
  reference panels lack (EVSE) and forward-declared capabilities (``doe``).
  ``adapter_schema*.json`` are served schemas (``GET /api/v2/homie/schema``),
  each from one SPAN Panel model; ``homie_schema_union_*.json`` is SPAN's
  r202639 union across models, which no panel serves. ``SEMANTICS`` must
  cover every file (``test_adapter_schema_is_fully_mapped``).

* ``custom_components/span_ebus/spec/*.json`` (the public eBus specification) is
  the STANDARD the adapter tracks. Where the adapter schema and a spec catalog
  both define a property, they must agree on datatype/unit
  (``test_adapter_schema_conforms_to_spec``), except for the adapter's expected
  refinements (a free ``string`` narrowed to an ``enum``; the spec's abstract
  ``energy`` unit token concretized to ``kWh``/``Wh``). An unexpected divergence
  is a signal: the adapter moved, or the pinned spec version is stale.

Provenance: ``.ebus-spec.json`` versions must match the vendored spec catalogs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.span_ebus.semantics import SEMANTICS

REPO = Path(__file__).parent.parent
COMPONENT = REPO / "custom_components" / "span_ebus"
SPEC = COMPONENT / "spec"
ADAPTER_SCHEMA = COMPONENT / "adapter_schema.json"
UNION_SCHEMA = COMPONENT / "homie_schema_union_r202639.json"
SCHEMA_FILES = sorted(COMPONENT.glob("adapter_schema*.json")) + sorted(
    COMPONENT.glob("homie_schema_union_*.json")
)
FIXTURES = REPO / "tests" / "fixtures"
LOCKFILE = REPO / ".ebus-spec.json"


def _device_classes(path: Path = ADAPTER_SCHEMA) -> dict:
    """Return a schema file's device classes (served files use camelCase, the union snake_case)."""
    data = json.loads(path.read_text())
    if "deviceClasses" in data:
        return data["deviceClasses"]
    return data["device_classes"]


def _schema_properties(path: Path = ADAPTER_SCHEMA) -> set[tuple[str, str, str]]:
    """Every (device_class, capability, property) a schema file declares."""
    return {
        (device_class, capability, prop)
        for device_class, caps in _device_classes(path).items()
        for capability, props in caps.items()
        for prop in props
    }


def _spec_catalog(capability: str) -> dict | None:
    """Return {property: decl} for a vendored spec capability catalog (patterns expanded)."""
    path = SPEC / "capabilities" / f"{capability}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    props = dict(data.get("properties") or {})
    for pattern_name, pattern in (data.get("property_patterns") or {}).items():
        base = pattern_name.split("{")[0]
        for token in pattern.get("expand", []):
            props[f"{base}{token}"] = pattern
    return props


def _divergence_allowed(kind: str, adapter_val: str, spec_val: str) -> bool:
    """Return True for expected adapter refinements of the spec (track-adapter-first)."""
    if kind == "datatype" and spec_val == "string" and adapter_val == "enum":
        return True  # adapter constrains a free string to an enum (e.g. info/model)
    if kind == "unit" and spec_val == "energy":
        return True  # spec's abstract "energy" token -> the adapter's concrete unit (kWh/Wh)
    return False


def _fixture_wire_properties(name: str) -> set[tuple[str, str, str]]:
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


def test_schema_files_are_present() -> None:
    """Both the MAIN 32 served schema and the r202639 union are vendored."""
    assert ADAPTER_SCHEMA in SCHEMA_FILES
    assert UNION_SCHEMA in SCHEMA_FILES


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_adapter_schema_is_fully_mapped(path: Path) -> None:
    """Every property a SPAN Panel can publish has a SEMANTICS entry (no silent drops)."""
    unmapped = sorted(k for k in _schema_properties(path) if k not in SEMANTICS)
    assert unmapped == [], f"{path.name} properties with no SEMANTICS entry: {unmapped}"


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_adapter_schema_conforms_to_spec(path: Path) -> None:
    """Where the adapter schema and a spec catalog both define a property, they agree.

    Adapter-only properties are SPAN extensions and allowed; known refinements
    (string->enum, abstract ``energy`` unit -> concrete) are allowed. Anything else
    is an unexpected divergence worth surfacing.
    """
    divergences: list[str] = []
    for device_class, caps in _device_classes(path).items():
        for capability, props in caps.items():
            spec = _spec_catalog(capability)
            if spec is None:
                continue
            for prop, adapter_decl in props.items():
                spec_decl = spec.get(prop)
                if spec_decl is None:
                    continue  # SPAN extension beyond the spec
                for field in ("datatype", "unit"):
                    a, s = adapter_decl.get(field), spec_decl.get(field)
                    if a and s and a != s and not _divergence_allowed(field, a, s):
                        divergences.append(f"{device_class}/{capability}/{prop}: {field} adapter={a} spec={s}")
    assert divergences == [], f"unexpected {path.name}<->spec divergences: " + "; ".join(divergences)


@pytest.mark.parametrize(
    ("fixture", "path"),
    [
        ("nt-2143-c1akc.json", ADAPTER_SCHEMA),
        ("synthetic-other-model.json", UNION_SCHEMA),
    ],
)
def test_fixture_is_a_subset_of_its_schema(fixture: str, path: Path) -> None:
    """A tree fixture only carries properties its schema declares."""
    schema = _schema_properties(path)
    stray = sorted(k for k in _fixture_wire_properties(fixture) if k not in schema)
    assert stray == [], f"{fixture} properties absent from {path.name}: {stray}"


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
