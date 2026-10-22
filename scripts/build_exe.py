"""Costruisce il bundle Windows autonomo.

    python scripts/build_exe.py

Produce:
    dist/DealFinder/                 la cartella da scompattare, con DealFinder.exe
    dist/DealFinder-windows.zip      quello che si allega alla release

A differenza dello ZIP di sorgenti che pubblicavamo prima, qui non c'è niente
da scegliere né da escludere a mano: nel pacchetto entra solo ciò che è
elencato in `DealFinder.spec`. Le note interne, la configurazione di sviluppo e
i test non possono finirci per costruzione.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "DealFinder.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
BUNDLE = DIST / "DealFinder"

#: Sotto questa soglia il bundle è troncato e non va pubblicato. Vale la stessa
#: ragione della guardia che aveva il vecchio costruttore dello ZIP: un archivio
#: incompleto si scarica benissimo, e il difetto lo scopre l'utente.
MINIMO_FILE = 500


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Costruisce il bundle Windows")
    parser.add_argument(
        "--output-dir", type=Path, help="Cartella separata per dist/ e build/ di verifica"
    )
    args = parser.parse_args(argv)
    output = args.output_dir.resolve() if args.output_dir else ROOT
    dist = output / "dist"
    build = output / "build"
    bundle = dist / "DealFinder"
    if not SPEC.exists():
        print(f"manca lo spec: {SPEC}", file=sys.stderr)
        return 1

    for cartella in (dist, build):
        # La pulizia riguarda esclusivamente i due output verificati.
        if cartella.resolve().parent != output.resolve() or cartella.is_symlink():
            raise ValueError(f"cartella di build non sicura: {cartella}")
        if cartella.exists():
            shutil.rmtree(cartella)

    print("PyInstaller in corso...")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(SPEC),
            "--noconfirm",
            "--distpath",
            str(dist),
            "--workpath",
            str(build),
        ],
        cwd=ROOT,
    )
    if not bundle.exists():
        print("PyInstaller non ha prodotto dist/DealFinder/", file=sys.stderr)
        return 2

    guida = ROOT / "scripts" / "bundle_LEGGIMI.txt"
    if guida.exists():
        shutil.copy2(guida, bundle / "LEGGIMI.txt")

    file_bundle = [f for f in bundle.rglob("*") if f.is_file()]
    if len(file_bundle) < MINIMO_FILE:
        print(
            f"il bundle ha solo {len(file_bundle)} file (minimo {MINIMO_FILE}): "
            "è troncato, non lo pubblico",
            file=sys.stderr,
        )
        return 3

    zip_path = dist / "DealFinder-windows.zip"
    print(f"creo {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archivio:
        for file in file_bundle:
            archivio.write(file, file.relative_to(bundle.parent))
    print(
        f"fatto: {zip_path} ({zip_path.stat().st_size // (1024 * 1024)} MB, "
        f"{len(file_bundle)} file)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
