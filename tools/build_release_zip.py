"""Crea uno ZIP distribuibile "pronto all'uso" del progetto.

Usato sia in locale sia dalla GitHub Action `release-asset.yml`.
Esclude file di sviluppo/runtime e mette tutto sotto una cartella radice
`trova-prezzi-<versione>/` così che estraendo si ottenga una cartella pulita.

Uso: python tools/build_release_zip.py <versione>   (es. v1.1.0)
"""
from __future__ import annotations

import os
import sys
import zipfile

VERSION = sys.argv[1] if len(sys.argv) > 1 else "dev"
NAME = f"trova-prezzi-{VERSION}"

EXCLUDE_DIRS = {
    ".git", ".github", "tests", "tools", "docs", ".venv", "__pycache__",
    "dist", ".playwright-mcp", ".pytest_cache", ".agents", ".claude", "node_modules",
}


def _skip(rel: str, fname: str) -> bool:
    if rel == ".streamlit/secrets.toml":          # mai includere i secret reali
        return True
    if rel.startswith("data/") and rel.endswith(".json"):  # dati runtime
        return True
    if fname.endswith((".pyc", ".zip")):
        return True
    return False


def main() -> None:
    os.makedirs("dist", exist_ok=True)
    out = os.path.join("dist", f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk("."):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, ".").replace("\\", "/")
                if _skip(rel, fname):
                    continue
                z.write(full, f"{NAME}/{rel}")
    print(out)


if __name__ == "__main__":
    main()
