#!/usr/bin/env python3
"""Package bridge/ as an osTicket plugin: dist/fastapi_integration-<version>.zip.

The zip's single top-level folder is ``fastapi_integration/`` — unzip it into osTicket's
``include/plugins/`` and enable it under Admin Panel → Manage → Plugins. The version comes
from ``FastApiIntegrationPlugin::VERSION`` in bridge/plugin.php, so it cannot drift.
"""

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge"
PLUGIN_DIR = "fastapi_integration"


def main() -> int:
    match = re.search(r"const VERSION = '([^']+)'", (BRIDGE / "plugin.php").read_text())
    if not match:
        print("could not find FastApiIntegrationPlugin::VERSION in bridge/plugin.php", file=sys.stderr)
        return 1
    version = match.group(1)
    out = ROOT / "dist" / f"{PLUGIN_DIR}-{version}.zip"
    out.parent.mkdir(exist_ok=True)
    files = sorted(p for p in BRIDGE.rglob("*.php") if "__pycache__" not in p.parts)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, f"{PLUGIN_DIR}/{path.relative_to(BRIDGE)}")
    print(f"{out.relative_to(ROOT)}  ({len(files)} files, version {version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
