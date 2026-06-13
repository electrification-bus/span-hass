# Contributing to span-hass

Thanks for your interest in contributing! `span-hass` is a [Home Assistant](https://www.home-assistant.io/) custom integration for [SPAN](https://www.span.io/) smart electrical panels, talking to the panel over its local MQTT broker via the [SPAN eBus API](https://github.com/spanio/SPAN-API-Client-Docs). It is built on top of [`ebus-sdk`](https://github.com/electrification-bus/python-sdk) (the Python SDK for the [Electrification Bus](https://ebus.energy) framework) and follows the [Homie Convention](https://homieiot.github.io) for device discovery and property semantics.

## How to contribute

### Discussions

Use [Discussions](https://github.com/electrification-bus/span-hass/discussions) for:

- Open-ended questions about the integration's design, entity model, or intent ("how should I expose X in Home Assistant?")
- Setup questions ("I can't get mDNS discovery to work on my network — what's the recommended pattern?")
- Energy Dashboard / Sankey configuration questions where the right answer depends on your panel topology and other integrations (see also [hass-atlas](https://github.com/electrification-bus/hass-atlas))
- Proposed new entities, services, or options-flow settings — worth aligning on the shape before writing the code
- Questions about the relationship between this integration and the underlying [`ebus-sdk`](https://github.com/electrification-bus/python-sdk) or the [Electrification Bus specification](https://github.com/electrification-bus/specification) (this integration consumes both — spec-level and SDK-level questions belong in their respective repos' Discussions)
- Thinking out loud about a proposed change before scoping it

Discussions are open-ended — a good place to align on direction before something becomes a concrete change. Aligned outcomes often turn into one or more Issues or pull requests.

### Issues

Use [Issues](https://github.com/electrification-bus/span-hass/issues) for actionable changes:

- Bug reports with reproduction steps (HA version, panel firmware version, integration version, relevant log excerpts)
- SPAN firmware quirks that the integration needs to work around (declared-vs-actual unit mismatches, ordering bugs, etc.) — note the firmware version where you observed it
- Concrete feature requests with a clear scope and a Home Assistant use case
- Documentation gaps where a specific README, docstring, or in-UI string change is intended
- Discussion outcomes that have alignment and a clear scope

If you're not sure whether something is an Issue or a Discussion, start with a Discussion — we can convert it later.

### Pull requests

Pull requests are welcome.

- For small fixes (typos, docstring tweaks, version bumps, low-risk bug fixes with a test), open a PR directly.
- For substantive changes (new entity types, new platforms, changes to existing entity unique-IDs or device-class assignments, new config-flow steps, new services, new dependencies), open a Discussion or Issue first so we can align on scope before you invest the effort.
- **Layer responsibly.** This integration is the Home Assistant *adapter* on top of `ebus-sdk`. Changes to MQTT transport, Homie discovery, property tracking, or device-tree semantics belong in [`ebus-sdk`](https://github.com/electrification-bus/python-sdk) (or in [`ebus-mqtt-client`](https://github.com/electrification-bus/ebus-mqtt-client) for pure transport concerns), not here. Keep this repo focused on the HA-facing concerns: entity model, config flow, options, services, device registry, Energy Dashboard hints.
- **Spec conformance is the north star.** Where the integration's behavior is normative (Homie property semantics, device states, energy direction conventions), it should track the [Electrification Bus specification](https://github.com/electrification-bus/specification). When working around a SPAN firmware bug, document it in [`docs/`](docs/) and prefer to file the bug upstream rather than baking the workaround in silently.
- **Entity unique-IDs are forever.** The unique-ID format `{serial}_{node_id}_{property_id}` is load-bearing — users have automations, dashboards, and history tied to these IDs. Changes to the format require a migration path; don't change it on a whim.
- **Lint and type-check before sending.** The repo enforces [ruff](https://github.com/astral-sh/ruff) and [mypy](https://mypy-lang.org/) — run `poetry run ruff check custom_components/span_ebus/` and `poetry run mypy custom_components/span_ebus/` locally before pushing. CI will catch what you miss, but green-first is friendlier.
- **Tests are required.** New behavior needs a test (`poetry run pytest tests/ -v`); new bug fixes need a regression test. Config-flow tests must use the `enable_custom_integrations` fixture. Match the existing pattern (`pytest-homeassistant-custom-component`) unless the change genuinely requires a live HA instance — in which case open a Discussion first.
- **Keep comments to a minimum.** The project style is to write self-explanatory code and reserve comments for non-obvious *why* (a SPAN firmware quirk, a Homie nuance, a paho-mqtt thread-safety workaround, an HA framework constraint). Don't add comments that just restate the code.
- **Version bumps touch two files.** The integration version lives in `custom_components/span_ebus/manifest.json` (consumed by HA) and the project version is in `pyproject.toml`. Keep them in sync, and add a `CHANGELOG.md` entry describing what changed.
- One commit per logical change is fine; we don't require squash or any particular branch naming.

## Releases

This integration is distributed via [HACS](https://hacs.xyz/) as a custom repository and tagged on GitHub. A maintainer bumps the version in `custom_components/span_ebus/manifest.json` + `pyproject.toml`, moves the `[Unreleased]` section of `CHANGELOG.md` under a new `[X.Y.Z] — YYYY-MM-DD` heading, and pushes a `vX.Y.Z` tag. HACS picks up tagged releases automatically; users on HACS see the update notification in the HA UI.

## Code of conduct

Be respectful and constructive. We appreciate everyone who takes the time to file an issue, start a discussion, or send a pull request.

## Maintenance posture

`span-hass` is an active alpha integration. Updates and maintenance, including responses to issues filed on GitHub, will take place on an "as time and resources permit" basis. The integration is developed alongside [`ebus-sdk`](https://github.com/electrification-bus/python-sdk), [`ebus-mqtt-client`](https://github.com/electrification-bus/ebus-mqtt-client), and the [Electrification Bus specification](https://github.com/electrification-bus/specification) — see the specification repo's README §Governance for the project's long-term governance context.
