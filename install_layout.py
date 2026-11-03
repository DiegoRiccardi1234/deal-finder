"""Pure, stdlib-only paths for source, legacy and versioned Windows layouts."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

LAYOUT_MARKER = ".dealfinder-layout.json"
LAYOUT_VERSION = 1
LAYOUT_PRODUCT = "dealfinder"
LEGACY_ASSET = "DealFinder-windows.zip"
ORGANIZED_ASSET = "DealFinder-organized-windows.zip"


def marker_payload() -> dict[str, str | int]:
    return {"product": LAYOUT_PRODUCT, "version": LAYOUT_VERSION}


def validate_marker_payload(payload: object) -> None:
    if (
        not isinstance(payload, dict)
        or payload.get("product") != LAYOUT_PRODUCT
        or type(payload.get("version")) is not int
        or payload["version"] != LAYOUT_VERSION
    ):
        raise ValueError("Layout Deal Finder sconosciuto: ripristinare il marker prima di avviare")


def is_organized(root: Path) -> bool:
    """A corrupt existing marker is an error, never an empty legacy install."""
    marker = Path(root) / "app" / LAYOUT_MARKER
    if is_redirected(Path(root) / "app") or is_redirected(marker):
        raise ValueError("Marker o cartella app reindirizzati non ammessi")
    if not marker.exists():
        if (Path(root) / "app" / "DealFinder.exe").exists():
            raise ValueError("Marker layout Deal Finder mancante: non creare dati di ripiego")
        return False
    try:
        validate_marker_payload(json.loads(marker.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Marker layout Deal Finder non valido: {marker}") from exc
    return True


def installation_root(executable_or_dir: Path | None = None) -> Path:
    target = Path(executable_or_dir or sys.executable).absolute()
    if any(is_redirected(p) for p in (target, *target.parents)):
        raise ValueError("Percorso installazione reindirizzato non ammesso")
    target = target.resolve()
    directory = target.parent if target.suffix.lower() == ".exe" or target.is_file() else target
    if directory.name.casefold() == "app":
        parent = directory.parent
        if (parent / "app" / LAYOUT_MARKER).exists() and is_organized(parent):
            return parent
        if (parent / "DealFinder.exe").exists() or (parent / "state").exists():
            raise ValueError("Marker layout Deal Finder mancante: non creare dati sotto app/")
    is_organized(directory)  # Validate an existing marker before callers create anything.
    return directory


def runtime_dir(root: Path) -> Path:
    root = Path(root)
    return root / "app" if is_organized(root) else root


def state_dir(root: Path) -> Path:
    root = Path(root)
    return root / "state" if is_organized(root) else root / "data"


def release_asset_name(root: Path) -> str:
    """Preserve the installed layout, including clients predating this helper."""
    return ORGANIZED_ASSET if is_organized(root) else LEGACY_ASSET


def is_redirected(path: Path) -> bool:
    """Detect symlinks and Windows junctions without following them."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )
