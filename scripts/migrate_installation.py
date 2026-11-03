"""Reversible local layout migration; never downloads, launches or publishes."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from install_layout import is_organized, is_redirected, runtime_dir, state_dir


def _hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as source:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(
                    source, "sha256"
                ).hexdigest()
    return result


def _safe_tree(root: Path) -> None:
    for item in (root, *root.parents):
        if is_redirected(item):
            raise ValueError(f"Link o junction non ammessi: {item}")
    pending = [root] if root.is_dir() else []
    while pending:
        directory = pending.pop()
        for item in directory.iterdir():
            if is_redirected(item):
                raise ValueError(f"Link o junction non ammessi: {item}")
            if item.is_dir():
                pending.append(item)


def _running_processes(root: Path) -> list[int]:
    if sys.platform != "win32":
        return []
    command = (
        "$target = [IO.Path]::GetFullPath($env:DEAL_FINDER_MIGRATION_TARGET).TrimEnd('\\') + '\\'; "
        "@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { "
        "$_.ExecutablePath -and $_.ExecutablePath.StartsWith($target, "
        "[StringComparison]::OrdinalIgnoreCase) } | Select-Object -ExpandProperty ProcessId) "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        env={**os.environ, "DEAL_FINDER_MIGRATION_TARGET": str(root)},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    values = json.loads(result.stdout.strip() or "[]")
    return [values] if isinstance(values, int) else list(values)


def _merge_preserved(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def migrate(candidate: Path, target: Path, backup_root: Path) -> dict:
    """Install candidate and migrate state after a verified complete backup.

    Original installation is retained as a complete tree in the backup; every
    exception after the swap restores it. No original user file is deleted.
    """
    candidate, target, backup_root = (Path(p).absolute() for p in (candidate, target, backup_root))
    for path in (candidate, target, backup_root):
        _safe_tree(path)
    candidate, target, backup_root = (p.resolve() for p in (candidate, target, backup_root))
    if any(
        a == b or a in b.parents or b in a.parents
        for a, b in ((candidate, target), (backup_root, target), (backup_root, candidate))
    ):
        raise ValueError("Candidato, installazione e backup devono essere alberi separati")
    if not is_organized(candidate):
        raise ValueError("Il candidato deve avere il nuovo layout marcato")
    if {p.name for p in candidate.iterdir()} != {"DealFinder.exe", "LEGGIMI.txt", "app", "state"}:
        raise ValueError("Root candidato non conforme")
    if any((candidate / "state").iterdir()):
        raise ValueError("Il candidato pubblico deve avere state vuota")
    if not all(
        (candidate / "app" / name).is_file()
        for name in ("DealFinder.exe", "Aggiorna.exe", "DealFinderMCP.exe")
    ):
        raise ValueError("Runtime candidato incompleto")
    if not (target / "DealFinder.exe").is_file():
        raise ValueError("Non è una installazione Deal Finder esistente")
    old_state, old_runtime = state_dir(target), runtime_dir(target)
    if (old_state / "aggiornamento.lock").exists():
        raise ValueError("Risolvere il lock di aggiornamento prima della migrazione")
    if _running_processes(target):
        raise ValueError("Chiudere Deal Finder e il suo updater prima della migrazione")
    if (
        not is_organized(target)
        and (target / "state").exists()
        and any((target / "state").iterdir())
    ):
        raise ValueError("Collisione data/state: nessuna fusione automatica")
    tag = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup = backup_root / tag
    backup.mkdir(parents=True)
    before = _hashes(target)
    shutil.copytree(target, backup / "snapshot")
    if _hashes(backup / "snapshot") != before or _hashes(target) != before:
        raise RuntimeError(f"Backup non verificato o dati cambiati: {backup}")
    database = old_state / "deal_finder.db"
    if database.exists():
        # SQLite may create WAL coordination sidecars even for a read-only
        # connection. Read a private copy, never the original or exact snapshot.
        database_copy = backup / "sqlite-read" / "deal_finder.db"
        database_copy.parent.mkdir()
        snapshot_database = backup / "snapshot" / database.relative_to(target)
        for suffix in ("", "-wal", "-shm"):
            source_file = Path(str(snapshot_database) + suffix)
            if source_file.exists():
                shutil.copy2(source_file, Path(str(database_copy) + suffix))
        with contextlib.closing(
            sqlite3.connect(f"{database_copy.as_uri()}?mode=ro", uri=True)
        ) as source:
            with contextlib.closing(
                sqlite3.connect(backup / "deal_finder-consistent.db")
            ) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Snapshot database non integro")
    manifest: dict[str, object] = {
        "target": str(target),
        "candidate": str(candidate),
        "before": before,
        "status": "prepared",
    }
    report = backup / "manifest.json"
    report.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-migration-", dir=target.parent))
    original = backup / "original-installation"
    swapped = False
    try:
        shutil.copytree(candidate, stage, dirs_exist_ok=True)
        _merge_preserved(old_state, stage / "state")
        for name in (".env", ".env.local", ".streamlit"):
            _merge_preserved(target / name, stage / "state" / name)
        _merge_preserved(old_runtime / "vendor", stage / "app" / "vendor")
        # Unknown personal files remain available without cluttering the root.
        known = {
            "dealfinder.exe",
            "aggiorna.exe",
            "dealfindermcp.exe",
            "leggimi.txt",
            "_internal",
            "app",
            "state",
            "data",
            "vendor",
            ".env",
            ".env.local",
            ".streamlit",
        }
        for entry in target.iterdir():
            if entry.name.casefold() not in known:
                _merge_preserved(entry, stage / "state" / "legacy-root" / entry.name)
        preserved_state = _hashes(old_state) if old_state.exists() else {}
        staged_state = _hashes(stage / "state")
        if any(staged_state.get(name) != digest for name, digest in preserved_state.items()):
            raise RuntimeError("Dati non preservati nello staging")
        preserved_vendor = (
            _hashes(old_runtime / "vendor") if (old_runtime / "vendor").exists() else {}
        )
        staged_vendor = _hashes(stage / "app" / "vendor")
        if any(staged_vendor.get(name) != digest for name, digest in preserved_vendor.items()):
            raise RuntimeError("Vendor non preservata nello staging")
        if _hashes(target) != before or _running_processes(target):
            raise RuntimeError("Installazione cambiata durante la preparazione")
        # The original tree and backup stay on their own volume. os.replace is
        # atomic only for the final staging/target swap (same parent).
        original_local = target.parent / f".{target.name}-original-{uuid.uuid4().hex}"
        target.rename(original_local)
        try:
            stage.rename(target)
            swapped = True
            if not is_organized(target):
                raise RuntimeError("Verifica layout finale fallita")
            final_state = _hashes(target / "state")
            if any(final_state.get(name) != digest for name, digest in preserved_state.items()):
                raise RuntimeError("Verifica dati finali fallita")
            manifest.update(
                status="migrated",
                state_files=len(preserved_state),
                vendor_files=len(preserved_vendor),
            )
            # A full disk while finalizing the journal must still roll back.
            report.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            shutil.move(str(original_local), str(original))
        except Exception:
            failed_local = target.parent / f".{target.name}-failed-{uuid.uuid4().hex}"
            if target.exists():
                target.rename(failed_local)
            if original_local.exists():
                original_local.rename(target)
            elif original.exists():
                shutil.copytree(original, target)
            else:
                shutil.copytree(backup / "snapshot", target)
            if failed_local.exists():
                with contextlib.suppress(OSError):
                    shutil.move(str(failed_local), str(backup / "failed-candidate"))
            raise
        return {
            "ok": True,
            "target": str(target),
            "backup": str(backup),
            "state_files": len(preserved_state),
            "vendor_files": len(preserved_vendor),
        }
    except Exception:
        manifest["status"] = "failed_after_swap" if swapped else "failed_before_swap"
        with contextlib.suppress(OSError):
            report.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        raise
    finally:
        if stage.exists():
            # Only a validated, uniquely-created staging tree is removable.
            if stage.parent != target.parent or not stage.name.startswith(
                f".{target.name}-migration-"
            ):
                raise RuntimeError("Staging non sicuro: conservato")
            shutil.rmtree(stage)


def restore(backup: Path, target: Path) -> dict:
    """Restore the original layout without discarding the current state tree."""
    backup, target = Path(backup).absolute(), Path(target).absolute()
    _safe_tree(backup)
    _safe_tree(target)
    backup, target = backup.resolve(), target.resolve()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if Path(manifest["target"]).resolve() != target:
        raise ValueError("Il backup appartiene a un'altra installazione")
    source = backup / "snapshot"
    if _hashes(source) != manifest["before"]:
        raise ValueError("Hash del backup non validi")
    if _running_processes(target):
        raise ValueError("Chiudere Deal Finder prima del ripristino")
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-restore-", dir=target.parent))
    shutil.copytree(source, stage, dirs_exist_ok=True)
    preserved = backup / ("before-restore-" + uuid.uuid4().hex)
    local = target.parent / f".{target.name}-before-restore-{uuid.uuid4().hex}"
    target.rename(local)
    try:
        stage.rename(target)
    except Exception:
        local.rename(target)
        raise
    shutil.move(str(local), str(preserved))
    return {"ok": True, "target": str(target), "preserved_current": str(preserved)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--restore", type=Path)
    args = parser.parse_args()
    if args.restore:
        result = restore(args.restore, args.target)
    elif args.candidate and args.backup_root:
        result = migrate(args.candidate, args.target, args.backup_root)
    else:
        parser.error("Servono --candidate e --backup-root, oppure --restore")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
