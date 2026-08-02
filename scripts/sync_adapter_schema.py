#!/usr/bin/env python3
"""Refresh the vendored adapter schema from a live SPAN panel.

Fetches ``GET http://<panel>/api/v2/homie/schema`` (unauthenticated; returns type
definitions only, no device values or serials) and writes it to
``custom_components/span_ebus/adapter_schema.json`` with a ``_provenance`` header.

The adapter schema is the source-of-truth span-hass models: it enumerates every
device class / capability / property the SPAN ebus-panel-adapter can publish.
When it changes, ``tests/test_semantics_coverage.py`` goes red until the
``SEMANTICS`` table is updated to match.

The human-maintained provenance fields (``intended_span_release``,
``release_status``) are carried forward from the existing file so the
release-status note persists across refreshes; the mechanical fields
(``captured_date``, ``firmware_version``) are updated from the fetched schema.

Usage:
    python3 scripts/sync_adapter_schema.py --panel-host lc1.yapjack.net
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "custom_components" / "span_ebus" / "adapter_schema.json"

_NOTE = (
    "Adapter-generated schema = the source-of-truth span-hass models: every device "
    "class / capability / property the SPAN ebus-panel-adapter can publish. Refresh "
    "with scripts/sync_adapter_schema.py."
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel-host", required=True, help="panel hostname or IP, e.g. lc1.yapjack.net")
    ap.add_argument("--panel-label", default=None, help="short label recorded in provenance (default: --panel-host)")
    args = ap.parse_args()

    url = f"http://{args.panel_host}/api/v2/homie/schema"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 - trusted local panel
        schema = json.loads(resp.read().decode())

    carried = {}
    if TARGET.exists():
        prev = json.loads(TARGET.read_text()).get("_provenance", {})
        carried = {k: prev[k] for k in ("intended_span_release", "release_status") if k in prev}

    out: dict = {
        "_provenance": {
            "source": "GET /api/v2/homie/schema",
            "captured_from_panel": args.panel_label or args.panel_host,
            "captured_date": datetime.date.today().isoformat(),
            "firmware_version": schema.get("firmwareVersion"),
            **carried,
            "note": _NOTE,
        }
    }
    out.update(schema)
    with TARGET.open("w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")

    device_classes = schema.get("deviceClasses", {})
    n_props = sum(len(props) for caps in device_classes.values() for props in caps.values())
    print(
        f"wrote {TARGET.relative_to(REPO_ROOT)}  firmware={schema.get('firmwareVersion')}  "
        f"deviceClasses={len(device_classes)}  properties={n_props}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
