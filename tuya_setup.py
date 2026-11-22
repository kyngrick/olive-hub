#!/usr/bin/env python3
"""One-time Tuya setup helper for olive_hub.py.

    python tuya_setup.py            # ask for keys, list devices, print .env lines
    python tuya_setup.py --write    # also append TUYA_* to .env

You need a free Tuya IoT Platform project:
https://iot.tuya.com -> Cloud -> Development -> create project
  * copy the Access ID / Access Secret from the project overview
  * under "Devices" -> "Link Tuya App Account", link the Smart Life / Tuya app
  * region is the data centre you picked (eu, us, cn or in)
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from olive_hub import env_str, load_dotenv  # noqa: E402


def ask(label: str, current: str = "") -> str:
    shown = f" [{current}]" if current else ""
    value = input(f"{label}{shown}: ").strip()
    return value or current


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure Tuya for olive_hub")
    parser.add_argument("--write", action="store_true", help="append TUYA_* keys to .env")
    args = parser.parse_args()

    load_dotenv(os.path.join(HERE, ".env"))

    region = ask("Tuya region (eu/us/cn/in)", env_str("TUYA_REGION", "eu")).lower()
    api_key = ask("Access ID", env_str("TUYA_API_KEY"))
    api_secret = env_str("TUYA_API_SECRET")
    if not api_secret:
        api_secret = getpass.getpass("Access Secret (hidden): ").strip()

    if not api_key or not api_secret:
        print("Access ID and Access Secret are required.")
        return 1

    try:
        import tinytuya
    except ImportError:
        print("tinytuya is missing. Install it with:")
        print(r"  .\.venv\Scripts\python.exe -m pip install tinytuya")
        return 1

    print(f"\nSigning in to Tuya ({region}) ...")
    cloud = tinytuya.Cloud(apiRegion=region, apiKey=api_key, apiSecret=api_secret)
    try:
        data = cloud.getdevices(verbose=False)
    except Exception as exc:
        print(f"Request failed: {exc}")
        return 1

    rows = data.get("result") if isinstance(data, dict) else data
    if not rows:
        print("No devices came back. In the Tuya project, link your app account and")
        print("make sure the devices are assigned to this project.")
        print("Raw response:", str(data)[:300])
        return 1

    print(f"\n{len(rows)} device(s):\n")
    print(f"  {'NAME':32} {'ID':22} {'CATEGORY':18} ONLINE")
    print(f"  {'-' * 32} {'-' * 22} {'-' * 18} {'-' * 6}")
    for device in rows:
        print(
            f"  {str(device.get('name', ''))[:32]:32} "
            f"{str(device.get('id', ''))[:22]:22} "
            f"{str(device.get('category', ''))[:18]:18} "
            f"{device.get('online', '?')}"
        )

    block = (
        "\n# ===== Tuya (added by tuya_setup.py) =====\n"
        f"TUYA_REGION={region}\n"
        f"TUYA_API_KEY={api_key}\n"
        f"TUYA_API_SECRET={api_secret}\n"
    )

    if args.write:
        path = os.path.join(HERE, ".env")
        existing = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig") as handle:
                existing = handle.read()
        if "TUYA_API_KEY" in existing:
            print("\n.env already has TUYA_API_KEY — update it by hand, nothing written.")
        else:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(block)
            print(f"\nWrote TUYA_* keys to {path}")
            print("Restart olive_hub.py, then try: turn on the kitchen light")
    else:
        print("\nAdd these to .env (or run again with --write):")
        print(block)

    print("\nTip: device names are matched fuzzily, so name them in the app as you")
    print("would say them, e.g. 'Kitchen Light', 'Desk Lamp', 'Bedroom Plug'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
