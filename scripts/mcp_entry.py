"""Standalone entry point; never imports the application or opens its database."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    if getattr(sys, "frozen", False):
        meipass = vars(sys).get("_MEIPASS")
        if not isinstance(meipass, str):
            raise RuntimeError("dealfinder_bundle_resources_missing")
        root = Path(meipass)
    else:
        root = Path(__file__).resolve().parents[1]
    transport = root / "offerte" / "mcp_server.py"
    runpy.run_path(str(transport), run_name="__main__")


if __name__ == "__main__":
    main()
