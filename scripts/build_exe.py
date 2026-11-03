"""Costruisce il bundle Windows autonomo.

    python scripts/build_exe.py

Produce:
    dist/DealFinder/                 la cartella da scompattare, con DealFinder.exe
    dist/DealFinder-organized-windows.zip  nuove installazioni organizzate
    dist/DealFinder-windows.zip            aggiornamenti legacy compatibili

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
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.package_windows import create_release_archives, organize_bundle
from install_layout import is_redirected

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
    output_argument = args.output_dir.absolute() if args.output_dir else ROOT
    if any(is_redirected(p) for p in (output_argument, *output_argument.parents)):
        raise ValueError("output di build reindirizzato non ammesso")
    output = args.output_dir.resolve() if args.output_dir else ROOT
    dist = output / "dist"
    build = output / "build"
    raw_bundle = dist / "DealFinder-runtime"
    bundle = dist / "DealFinder"
    if not SPEC.exists():
        print(f"manca lo spec: {SPEC}", file=sys.stderr)
        return 1

    for cartella in (dist, build):
        # La pulizia riguarda esclusivamente i due output verificati.
        if cartella.resolve().parent != output.resolve() or is_redirected(cartella):
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
    if not raw_bundle.exists():
        print("PyInstaller non ha prodotto il runtime", file=sys.stderr)
        return 2
    organize_bundle(raw_bundle, bundle)

    file_bundle = [f for f in bundle.rglob("*") if f.is_file()]
    if len(file_bundle) < MINIMO_FILE:
        print(
            f"il bundle ha solo {len(file_bundle)} file (minimo {MINIMO_FILE}): "
            "è troncato, non lo pubblico",
            file=sys.stderr,
        )
        return 3

    for zip_path in create_release_archives(raw_bundle, bundle, dist).values():
        print(f"fatto: {zip_path} ({zip_path.stat().st_size // (1024 * 1024)} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
