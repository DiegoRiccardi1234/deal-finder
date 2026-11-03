"""Arrange a raw PyInstaller bundle without copying any user state."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from install_layout import (
    LEGACY_ASSET,
    ORGANIZED_ASSET,
    LAYOUT_MARKER,
    is_redirected,
    marker_payload,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = {"DealFinder.exe", "LEGGIMI.txt", "app", "state"}
RUNTIME_FILES = ("DealFinder.exe", "Aggiorna.exe", "DealFinderMCP.exe")
_PRIVATE = {"data", "state", ".env", ".env.local", ".streamlit", ".giorno-notte", "leggimi.txt"}


def compiler_path() -> Path:
    return (
        Path(os.environ.get("WINDIR", r"C:\Windows"))
        / "Microsoft.NET"
        / "Framework64"
        / "v4.0.30319"
        / "csc.exe"
    )


def organize_bundle(source: Path, target: Path) -> Path:
    source, target = Path(source).absolute(), Path(target).absolute()
    if any(is_redirected(p) for root in (source, target) for p in (root, *root.parents)):
        raise ValueError("Link o junction non ammessi nel pacchetto")
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Le cartelle raw e finale devono essere separate")
    if not all((source / name).is_file() for name in RUNTIME_FILES):
        raise ValueError("Bundle raw incompleto: mancano runtime, updater o MCP")
    if not (source / "_internal").is_dir():
        raise ValueError("Bundle raw senza _internal")
    allowed = {*RUNTIME_FILES, "_internal", "vendor", "LEGGIMI.txt"}
    if any(
        entry.name not in allowed and entry.name.casefold() not in _PRIVATE
        for entry in source.iterdir()
    ):
        raise ValueError("File estranei nel bundle raw: non confezionare una installazione utente")
    if target.exists() and any(target.iterdir()):
        raise ValueError("La destinazione deve essere vuota: nessun file viene cancellato")
    if any(is_redirected(p) for p in (source, target, *source.rglob("*"))):
        raise ValueError("Link o junction non ammessi nel pacchetto")
    compiler = compiler_path()
    if not compiler.is_file():
        raise RuntimeError("Compilatore C# .NET Framework Windows non disponibile")
    app = target / "app"
    app.mkdir(parents=True)
    (target / "state").mkdir()
    for entry in source.iterdir():
        if entry.name.casefold() in _PRIVATE:
            continue
        destination = app / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination)
        else:
            shutil.copy2(entry, destination)
    (app / LAYOUT_MARKER).write_text(json.dumps(marker_payload()) + "\n", encoding="utf-8")
    subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:winexe",
            "/reference:System.Windows.Forms.dll",
            "/reference:System.Web.Extensions.dll",
            f"/out:{target / 'DealFinder.exe'}",
            str(ROOT / "scripts" / "windows_launcher.cs"),
        ],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    shutil.copy2(ROOT / "scripts" / "bundle_LEGGIMI.txt", target / "LEGGIMI.txt")
    if {p.name for p in target.iterdir()} != PUBLIC_ROOT:
        raise RuntimeError("Struttura pubblica del pacchetto non valida")
    return target


def create_release_archives(raw: Path, organized: Path, dist: Path) -> dict[str, Path]:
    """Publish both layouts from one runtime, retaining the historical ZIP name.

    Old updaters hard-code DealFinder-windows.zip and require a flat tree.
    A separate explicit filename keeps both generations installable.
    """
    from update_sync import valida_archivio

    raw, organized, dist = Path(raw), Path(organized), Path(dist)
    shutil.copy2(ROOT / "scripts" / "bundle_LEGGIMI.txt", raw / "LEGGIMI.txt")
    assets = {}
    allowed = {*RUNTIME_FILES, "_internal", "vendor", "LEGGIMI.txt"}
    for name, folder, expected in (
        (LEGACY_ASSET, raw, "legacy"),
        (ORGANIZED_ASSET, organized, "organized"),
    ):
        archive = dist / name
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            if expected == "organized":
                output.write(folder / "state", "DealFinder/state/")
            for file in sorted(folder.rglob("*")):
                if is_redirected(file):
                    raise ValueError(f"link non ammesso nel pacchetto: {file}")
                if not file.is_file():
                    continue
                relative = file.relative_to(folder)
                if expected == "legacy" and relative.parts[0] not in allowed:
                    continue
                output.write(file, "DealFinder/" + relative.as_posix())
        valida_archivio(archive, expected_layout=expected)
        assets[name] = archive
    return assets
