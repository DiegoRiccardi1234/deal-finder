"""Crea uno ZIP distribuibile "pronto all'uso" del progetto.

Usato sia in locale sia dalla GitHub Action `release-asset.yml`.
Mette tutto sotto una cartella radice `deal-finder-<versione>/`, così che
estraendo si ottenga una cartella pulita.

L'elenco dei file viene da **`git ls-files`**, non da una passeggiata sul
filesystem. La differenza non è di stile: camminando sulla cartella lo ZIP
raccoglieva anche ciò che git ignora, e cioè 7,9 MB di `.mypy_cache` e — molto
peggio — `HANDOFF.md`, `APPUNTI.md` e i `CLAUDE.md`, ossia le note di lavoro
interne che dal repo erano state tolte apposta. Sarebbero finite in un artefatto
pubblico. Con `git ls-files` l'archivio contiene per costruzione esattamente ciò
che è versionato: niente cache, niente file ignorati, nessun segreto.

Come effetto collaterale sparisce anche il crash sul device Windows riservato
`nul`, che sta nella root di questo repo ed è ignorato: `os.path.relpath` ci
moriva sopra troncando l'archivio a metà.

Uso: python tools/build_release_zip.py <versione>   (es. v3.0.0)
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile

VERSION = sys.argv[1] if len(sys.argv) > 1 else "dev"
NAME = f"deal-finder-{VERSION}"

#: Cartelle versionate ma inutili a chi scarica lo ZIP per usare l'app.
EXCLUDE_PREFIXES = (
    ".github/",
    "tests/",
    "tools/",
    "docs/",
)

#: Percorsi versionati da non spedire comunque.
EXCLUDE_EXACT = {
    ".gitignore",
    ".dockerignore",
}


def _tracked_files() -> list[str]:
    """Percorsi versionati, relativi alla root del repo."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        check=True,
        text=True,
    )
    return [p for p in out.stdout.split("\0") if p]


def _skip(rel: str) -> bool:
    if rel.startswith(EXCLUDE_PREFIXES) or rel in EXCLUDE_EXACT:
        return True
    # Dati runtime: il seed della knowledge base serve, il resto no.
    if rel.startswith("data/") and not rel.endswith("knowledge_base.json"):
        return True
    return False


def main() -> None:
    os.makedirs("dist", exist_ok=True)
    out = os.path.join("dist", f"{NAME}.zip")

    inclusi = [p for p in _tracked_files() if not _skip(p)]
    if len(inclusi) < 40:
        # Guardia: un archivio quasi vuoto significa che qualcosa è andato storto
        # a monte. Meglio fallire che pubblicare una release troncata.
        raise SystemExit(f"ZIP sospetto: solo {len(inclusi)} file da includere")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in inclusi:
            if os.path.isfile(rel):
                z.write(rel, f"{NAME}/{rel}")

    print(f"{out}  ({len(inclusi)} file)")


if __name__ == "__main__":
    main()
