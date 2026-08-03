# Vendored eBus specification catalogs

These JSON files are vendored (copied) from the public [`electrification-bus/specification`](https://github.com/electrification-bus/specification) repository. They are the machine-readable capability and device catalogs for the eBus standard the SPAN adapter tracks. The integration models the adapter's *own* generated schema (`../adapter_schema.json`); these catalogs are the upstream standard and the conformance reference.

## Why vendored

span-hass ships as a Home Assistant custom integration, so it cannot fetch the specification at runtime. The catalogs it depends on are copied in here and shipped with the integration, which keeps it fully self-contained.

## What is here

- `capabilities/*.json` : one property catalog per `energy.ebus.capability.*` capability (each property's `datatype`, `unit`, `format`, `settable`, `req`, and per-phase `property_patterns`).
- `devices/*.json` : one device profile per `energy.ebus.device.*` type, listing the capabilities it composes and each capability's pinned catalog version.
- `schemas/*.schema.json` : the JSON Schemas the two catalog kinds are validated against.

## Provenance and pinning

The exact source commit is recorded in `.ebus-spec.json` at the repository root (`synced_commit`), along with the capability and device versions this integration implements. The pin tracks the commit the SPAN ebus-panel-adapter targets: the specification is what the adapter tracks, and this integration tracks the adapter, so the vendored catalogs match what the panels actually publish on the wire.

## How the integration uses these

Entity **structure** (which properties exist, their datatype/unit/format/settable) is read at runtime from each device's live Homie `$description`, the source of truth for what a given panel actually emits. The **coverage oracle** is not these files but the adapter's own generated schema, vendored at `../adapter_schema.json` (`GET /api/v2/homie/schema`): it enumerates everything the ebus-panel-adapter can publish, and the mapper's declarative table is validated in CI to cover all of it. These spec catalogs are the **upstream standard the adapter tracks** and a **conformance reference**: where the adapter schema and a catalog both define a property, CI checks they agree on datatype/unit (allowing the adapter's expected refinements, e.g. a free `string` narrowed to an `enum`). See `tests/test_semantics_coverage.py`.

## Updating (re-vendoring)

Do not edit these files by hand. To track a newer specification version, bump the pin and re-run the sync script from the repository root:

```bash
python3 scripts/sync_spec.py --spec-repo ../specification --commit <new-commit>
```

This rewrites this directory and refreshes `.ebus-spec.json`. Review the resulting diff: it is the version bump. The `spec-drift` CI job reports when the upstream specification has moved ahead of the pinned commit, which is the signal to re-vendor.
