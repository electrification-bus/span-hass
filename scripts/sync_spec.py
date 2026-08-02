#!/usr/bin/env python3
"""Vendor eBus specification catalogs into span-hass, and refresh .ebus-spec.json.

span-hass is a *shipped* Home Assistant integration, so it cannot fetch the
specification at runtime. Instead it VENDORS the public
``electrification-bus/specification`` machine-readable catalogs
(``capabilities/*.json``, ``devices/*.json``, ``conventions/schemas/*.json``)
into ``custom_components/span_ebus/spec/`` at a pinned commit, and records that
provenance in ``.ebus-spec.json`` at the repo root.

Re-vendoring IS the version bump: point at a newer spec commit, run this, and
review the resulting JSON diff. The pinned commit tracks the version the SPAN
ebus-panel-adapter targets (adapter-first): the specification is what the
adapter tracks, and span-hass tracks the adapter.

Reads are done with ``git show <commit>:<path>`` so the spec checkout's working
tree is never touched.

Usage:
    python3 scripts/sync_spec.py --spec-repo ../specification --commit 16b00305
    python3 scripts/sync_spec.py --spec-repo ../specification   # re-vendor at the pinned commit
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "custom_components" / "span_ebus" / "spec"
LOCKFILE = REPO_ROOT / ".ebus-spec.json"
SPEC_REPO_URL = "https://github.com/electrification-bus/specification"

# (source subdir in the spec repo, destination subdir under VENDOR_DIR)
VENDOR_PATHS = [
    ("capabilities", "capabilities"),
    ("devices", "devices"),
    ("conventions/schemas", "schemas"),
]

# What span-hass implements today. Versions are read from the vendored files, so
# re-vendoring auto-refreshes them; only membership is hand-maintained here.
# lugs / pv / mid are SPAN tree roles with no standalone spec device profile, so
# they are covered transitively by the capabilities they carry, not pinned as devices.
PIN_CAPABILITIES = [
    "info", "door", "status", "meter", "connection", "switch", "breaker",
    "load-shed", "pcs", "power-flows", "shed", "shed-forecast", "soc", "grid",
]
PIN_DEVICES = ["circuit", "distribution-enclosure", "bess"]

FRAMEWORK_FALLBACK = "0.7"


def git(spec_repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(spec_repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def git_maybe(spec_repo: Path, *args: str) -> str | None:
    r = subprocess.run(["git", "-C", str(spec_repo), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec-repo", required=True, help="path to a local electrification-bus/specification checkout")
    ap.add_argument("--commit", help="commit-ish to vendor at; default = synced_commit in .ebus-spec.json")
    args = ap.parse_args()

    spec_repo = Path(args.spec_repo).resolve()
    if not (spec_repo / ".git").exists():
        return _die(f"not a git checkout: {spec_repo}")

    commit = args.commit
    if not commit and LOCKFILE.exists():
        commit = json.loads(LOCKFILE.read_text())["synced_commit"]
    if not commit:
        return _die("no --commit given and no existing .ebus-spec.json to read synced_commit from")

    sha = git(spec_repo, "rev-parse", commit).strip()
    subject = git(spec_repo, "log", "-1", "--format=%s", sha).strip()
    print(f"vendoring specification @ {sha[:9]}  ({subject})")

    # Copy the JSON catalogs.
    copied: list[str] = []
    for src, dst in VENDOR_PATHS:
        dstdir = VENDOR_DIR / dst
        dstdir.mkdir(parents=True, exist_ok=True)
        for stale in dstdir.glob("*.json"):
            stale.unlink()
        listing = git(spec_repo, "ls-tree", "-r", "--name-only", sha, "--", src).splitlines()
        for path in listing:
            if not path.endswith(".json"):
                continue
            content = git(spec_repo, "show", f"{sha}:{path}")
            (dstdir / Path(path).name).write_text(content)
            copied.append(f"{dst}/{Path(path).name}")
    print(f"  vendored {len(copied)} JSON files into {VENDOR_DIR.relative_to(REPO_ROOT)}/")

    # Framework version: prefer the spec's generated manifest at this commit; else fall back.
    framework = FRAMEWORK_FALLBACK
    manifest_raw = git_maybe(spec_repo, "show", f"{sha}:spec-manifest.json")
    if manifest_raw:
        try:
            framework = json.loads(manifest_raw)["artifacts"]["framework"]["framework"]["version"]
        except (KeyError, json.JSONDecodeError):
            pass
    print(f"  framework version: {framework}")

    # Build the implements map from the vendored files (authoritative versions).
    def _ver(kind: str, name: str) -> str:
        return json.loads((VENDOR_DIR / kind / f"{name}.json").read_text())["version"]

    implements = {
        "capabilities": {c: _ver("capabilities", c) for c in PIN_CAPABILITIES},
        "devices": {d: _ver("devices", d) for d in PIN_DEVICES},
    }

    lock = {
        "$schema": "https://ebus.energy/schemas/ebus-spec.json",
        "spec_repo": SPEC_REPO_URL,
        "synced_commit": sha,
        "synced_date": datetime.date.today().isoformat(),
        "role": "controller",
        "framework": framework,
        "implements": implements,
        "notes": (
            "Vendored eBus specification catalogs (public: electrification-bus/specification) "
            "under custom_components/span_ebus/spec/. Tracks the tree-v1 (parent/child) capability "
            "model at the commit the SPAN ebus-panel-adapter targets. Regenerate with "
            "scripts/sync_spec.py after bumping the pin. lugs/pv/mid are tree roles with no "
            "standalone spec device profile, so they are covered by the capabilities they carry."
        ),
    }
    LOCKFILE.write_text(json.dumps(lock, indent=2) + "\n")
    print(f"  wrote {LOCKFILE.relative_to(REPO_ROOT)}")
    print("    capabilities:", ", ".join(f"{k} {v}" for k, v in implements["capabilities"].items()))
    print("    devices:     ", ", ".join(f"{k} {v}" for k, v in implements["devices"].items()))
    return 0


def _die(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
