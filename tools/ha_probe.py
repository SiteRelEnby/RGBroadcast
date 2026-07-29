#!/usr/bin/env python3
"""Probe a real Home Assistant light for the facts RGBroadcast depends on.

Reads connection details from an env file (default ``~/.config/rgbroadcast-dev.env``)
holding ``HA_URL`` and ``HA_TOKEN``. Never prints the token.

Two modes:

  info       Read-only. Dump capability, tier and kelvin bounds for a light.
  transition Drive the light: the transition-honesty test. Issues a slow
             transition and samples brightness to see whether the device
             interpolates or snaps. This physically changes the light.

Usage:
  python tools/ha_probe.py info <entity_id> [<entity_id> ...]
  python tools/ha_probe.py transition <entity_id>
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

DEFAULT_ENV = Path.home() / ".config" / "rgbroadcast-dev.env"

_COLOUR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}
_TRANSITION_BIT = 32


def load_env(path: Path) -> tuple[str, str]:
    """Return (url, token) from the env file, without echoing the token."""
    if not path.exists():
        sys.exit(f"env file not found: {path}")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    try:
        return values["HA_URL"].rstrip("/"), values["HA_TOKEN"]
    except KeyError as missing:
        sys.exit(f"env file missing {missing}")


def _request(url: str, token: str, path: str, payload: dict | None = None) -> object:
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{url}{path}", data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        sys.exit(f"HTTP {err.code} on {path}: {err.read().decode()[:200]}")
    except urllib.error.URLError as err:
        sys.exit(f"cannot reach {url}: {err.reason}")


def get_state(url: str, token: str, entity_id: str) -> dict:
    return _request(url, token, f"/api/states/{entity_id}")  # type: ignore[return-value]


def detect_tier(modes: set[str]) -> str:
    has_colour = bool(modes & _COLOUR_MODES)
    has_cct = "color_temp" in modes
    if has_colour and has_cct:
        return "hybrid"
    if has_colour:
        return "colour"
    if has_cct:
        return "cct"
    if "brightness" in modes:
        return "brightness"
    return "onoff"


def cmd_info(url: str, token: str, entity_ids: list[str]) -> None:
    for entity_id in entity_ids:
        state = get_state(url, token, entity_id)
        attrs = state.get("attributes", {})
        modes = {str(m) for m in attrs.get("supported_color_modes") or []}
        features = int(attrs.get("supported_features") or 0)
        print(f"\n{entity_id}  ({state.get('state')})")
        print(f"  friendly_name : {attrs.get('friendly_name')}")
        print(f"  color_modes   : {sorted(modes)}")
        print(f"  tier          : {detect_tier(modes)}")
        print(f"  features      : {features}")
        print(f"  advertises transition : {bool(features & _TRANSITION_BIT)}")
        print(
            f"  kelvin bounds : {attrs.get('min_color_temp_kelvin')} - "
            f"{attrs.get('max_color_temp_kelvin')}"
        )


def cmd_transition(url: str, token: str, entity_id: str) -> None:
    """The single most important manual test (design doc section 10.1)."""
    print(f"Transition-honesty test on {entity_id}.")
    print("Setting brightness to 100% (no transition)...")
    _request(
        url,
        token,
        "/api/services/light/turn_on",
        {"entity_id": entity_id, "brightness_pct": 100},
    )
    time.sleep(2)
    print("Requesting fade to 1% over 10s. Sampling brightness each second...")
    _request(
        url,
        token,
        "/api/services/light/turn_on",
        {"entity_id": entity_id, "brightness_pct": 1, "transition": 10},
    )
    samples: list[int] = []
    for _ in range(11):
        state = get_state(url, token, entity_id)
        bri = state.get("attributes", {}).get("brightness")
        samples.append(int(bri) if bri is not None else -1)
        time.sleep(1)
    print(f"  brightness samples (0-255): {samples}")

    distinct = len({s for s in samples if s >= 0})
    if distinct <= 2:
        print(
            "  VERDICT: SNAPPED. The device ignored the transition despite any "
            "advertised support. Use 'Force stepped rendering'."
        )
    else:
        print(
            f"  VERDICT: INTERPOLATED across {distinct} distinct levels. The "
            "device honours transitions; the fade renderer will work."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="read-only capability dump")
    info.add_argument("entity_ids", nargs="+")

    trans = sub.add_parser("transition", help="drive the light: transition test")
    trans.add_argument("entity_id")

    args = parser.parse_args()
    url, token = load_env(args.env)
    if os.environ.get("HA_URL"):  # allow env override without touching the file
        url = os.environ["HA_URL"].rstrip("/")

    if args.command == "info":
        cmd_info(url, token, args.entity_ids)
    elif args.command == "transition":
        cmd_transition(url, token, args.entity_id)


if __name__ == "__main__":
    main()
