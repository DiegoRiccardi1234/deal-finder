"""Layout migration and update contracts without touching an installed app."""

from __future__ import annotations

import json
import contextlib
import sqlite3
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import install_layout as layout
import update_sync
from scripts import migrate_installation as migration
from scripts import package_windows as packaging


def _organized(root: Path, *, value: str = "new") -> Path:
    (root / "state").mkdir(parents=True)
    (root / "app" / "_internal").mkdir(parents=True)
    for name in packaging.RUNTIME_FILES:
        (root / "app" / name).write_text(value, encoding="utf-8")
    (root / "app" / "_internal" / "python311.dll").write_text(value, encoding="utf-8")
    (root / "DealFinder.exe").write_text("launcher " + value, encoding="utf-8")
    (root / "LEGGIMI.txt").write_text(value, encoding="utf-8")
    (root / "app" / layout.LAYOUT_MARKER).write_text(
        json.dumps(layout.marker_payload()), encoding="utf-8"
    )
    return root


def _legacy(root: Path) -> Path:
    (root / "data").mkdir(parents=True)
    (root / "_internal").mkdir()
    (root / "DealFinder.exe").write_text("old", encoding="utf-8")
    (root / "Aggiorna.exe").write_text("old updater", encoding="utf-8")
    (root / "_internal" / "library.dll").write_text("old library", encoding="utf-8")
    (root / "data" / "local_secrets.json").write_text('{"key":"synthetic"}', encoding="utf-8")
    with contextlib.closing(sqlite3.connect(root / "data" / "deal_finder.db")) as db:
        db.execute("CREATE TABLE sample(value TEXT)")
        db.execute("INSERT INTO sample VALUES ('preserved')")
        db.commit()
    return root


def test_layout_resolves_both_formats_and_source_app_folder(tmp_path):
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    assert layout.state_dir(source) == source / "data"
    assert layout.runtime_dir(source) == source
    root = _organized(tmp_path / "organized")
    assert layout.installation_root(root / "app" / "DealFinder.exe") == root
    assert layout.installation_root(root) == root
    assert layout.state_dir(root) == root / "state"


@pytest.mark.parametrize(
    "payload",
    [
        "broken",
        '{"product":"dealfinder","version":2}',
        '{"product":"other","version":1}',
        '{"product":"dealfinder","version":true}',
    ],
)
def test_invalid_marker_never_falls_back_or_creates_data(tmp_path, payload):
    root = _organized(tmp_path / "organized")
    (root / "app" / layout.LAYOUT_MARKER).write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="Marker"):
        layout.state_dir(root)
    assert not (root / "data").exists()
    assert not (root / "app" / "data").exists()


def test_missing_marker_for_installed_runtime_is_error(tmp_path):
    root = _organized(tmp_path / "organized")
    (root / "app" / layout.LAYOUT_MARKER).unlink()
    with pytest.raises(ValueError, match="mancante"):
        layout.installation_root(root / "app" / "DealFinder.exe")


def test_flat_installation_may_be_named_app(tmp_path):
    root = _legacy(tmp_path / "app")
    assert layout.installation_root(root / "DealFinder.exe") == root
    assert layout.release_asset_name(root) == layout.LEGACY_ASSET


@pytest.mark.parametrize("organized_target", [False, True])
def test_organized_release_preserves_target_layout_and_data(tmp_path, organized_target):
    source = _organized(tmp_path / "source")
    target = (
        _organized(tmp_path / "target", value="old")
        if organized_target
        else _legacy(tmp_path / "target")
    )
    data = layout.state_dir(target)
    (data / "keep.txt").write_text("private", encoding="utf-8")
    vendor = layout.runtime_dir(target) / "vendor"
    vendor.mkdir()
    (vendor / "custom.dll").write_text("preserved", encoding="utf-8")
    (source / "app" / "vendor").mkdir()
    (source / "app" / "vendor" / "custom.dll").write_text("must not overwrite", encoding="utf-8")
    update_sync.sincronizza(origine=source, destinazione=target)
    assert (layout.runtime_dir(target) / "DealFinder.exe").read_text() == "new"
    assert (data / "keep.txt").read_text() == "private"
    assert (vendor / "custom.dll").read_text() == "preserved"
    assert (target / "DealFinder.exe").read_text() == (
        "launcher new" if organized_target else "new"
    )
    assert not (target / layout.LAYOUT_MARKER).exists()


def test_historic_flat_runtime_cannot_replace_organized_install(tmp_path):
    source, target = _legacy(tmp_path / "source"), _organized(tmp_path / "target")
    before = migration._hashes(target)
    with pytest.raises(ValueError, match="Runtime legacy"):
        update_sync.sincronizza(origine=source, destinazione=target)
    assert migration._hashes(target) == before


def test_organized_zip_validates_and_rejects_embedded_state(tmp_path):
    root = _organized(tmp_path / "candidate")
    archive = tmp_path / "candidate.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(root / "state", "DealFinder/state/")
        for file in root.rglob("*"):
            if file.is_file():
                z.write(file, "DealFinder/" + file.relative_to(root).as_posix())
    assert update_sync.valida_archivio(archive) == "DealFinder"
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("DealFinder/state/private.db", "secret")
    with pytest.raises(ValueError, match="stato utente"):
        update_sync.valida_archivio(archive)


def test_migration_preserves_state_vendor_and_can_restore_exact_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "_running_processes", lambda root: [])
    candidate, target = _organized(tmp_path / "candidate"), _legacy(tmp_path / "installed")
    (target / "vendor").mkdir()
    (target / "vendor" / "custom.dll").write_text("user vendor", encoding="utf-8")
    (target / ".streamlit").mkdir()
    (target / ".streamlit" / "secrets.toml").write_text('key="private"', encoding="utf-8")
    (target / "personal.txt").write_text("personal", encoding="utf-8")
    before = migration._hashes(target)
    result = migration.migrate(candidate, target, tmp_path / "private-backups")
    assert {p.name for p in target.iterdir()} == packaging.PUBLIC_ROOT
    assert (target / "state" / "local_secrets.json").read_text() == '{"key":"synthetic"}'
    assert (target / "app" / "vendor" / "custom.dll").read_text() == "user vendor"
    assert (target / "state" / ".streamlit" / "secrets.toml").read_text() == 'key="private"'
    assert (target / "state" / "legacy-root" / "personal.txt").read_text() == "personal"
    assert not (target / "state" / "data").exists()
    with contextlib.closing(sqlite3.connect(target / "state" / "deal_finder.db")) as db:
        assert db.execute("SELECT value FROM sample").fetchone() == ("preserved",)
    assert (Path(result["backup"]) / "deal_finder-consistent.db").exists()
    (target / "state" / "after-migration.txt").write_text("keep this too", encoding="utf-8")
    restored = migration.restore(Path(result["backup"]), target)
    assert migration._hashes(target) == before
    assert (
        Path(restored["preserved_current"]) / "state" / "after-migration.txt"
    ).read_text() == "keep this too"


@pytest.mark.parametrize("failure", ["running", "collision", "corrupt"])
def test_migration_preconditions_leave_original_untouched(tmp_path, monkeypatch, failure):
    candidate, target = _organized(tmp_path / "candidate"), _legacy(tmp_path / "installed")
    monkeypatch.setattr(
        migration, "_running_processes", lambda root: [42] if failure == "running" else []
    )
    if failure == "collision":
        (target / "state").mkdir()
        (target / "state" / "other.db").write_text("collision", encoding="utf-8")
    if failure == "corrupt":
        (candidate / "app" / layout.LAYOUT_MARKER).write_text("broken", encoding="utf-8")
    before = migration._hashes(target)
    with pytest.raises(ValueError):
        migration.migrate(candidate, target, tmp_path / "backups")
    assert migration._hashes(target) == before
    assert not (tmp_path / "backups").exists()


def test_failed_directory_swap_restores_original(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "_running_processes", lambda root: [])
    candidate, target = _organized(tmp_path / "candidate"), _legacy(tmp_path / "installed")
    before = migration._hashes(target)
    rename = Path.rename

    def fail_stage(path, destination):
        if path.name.startswith(".installed-migration-"):
            raise OSError("synthetic rename failure")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_stage)
    with pytest.raises(OSError, match="synthetic"):
        migration.migrate(candidate, target, tmp_path / "backups")
    assert migration._hashes(target) == before
    assert list((tmp_path / "backups").glob("*/manifest.json"))


def test_full_disk_finalizing_journal_rolls_back_original(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "_running_processes", lambda root: [])
    candidate, target = _organized(tmp_path / "candidate"), _legacy(tmp_path / "installed")
    before = migration._hashes(target)
    write = Path.write_text

    def fail_final_journal(path, data, *args, **kwargs):
        if path.name == "manifest.json" and json.loads(data).get("status") == "migrated":
            raise OSError("synthetic full disk")
        return write(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_final_journal)
    with pytest.raises(OSError, match="full disk"):
        migration.migrate(candidate, target, tmp_path / "backups")
    assert migration._hashes(target) == before


def test_orphan_wal_is_included_in_consistent_snapshot_without_mutating_original(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(migration, "_running_processes", lambda root: [])
    candidate, target = _organized(tmp_path / "candidate"), _legacy(tmp_path / "installed")
    live = tmp_path / "wal-source.db"
    with contextlib.closing(sqlite3.connect(live)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE sample(value TEXT)")
        db.execute("INSERT INTO sample VALUES ('from-wal')")
        db.commit()
        for suffix in ("", "-wal", "-shm"):
            shutil.copy2(Path(str(live) + suffix), target / "data" / ("deal_finder.db" + suffix))
    before = migration._hashes(target)
    result = migration.migrate(candidate, target, tmp_path / "backups")
    backup = Path(result["backup"])
    assert migration._hashes(backup / "snapshot") == before
    with contextlib.closing(sqlite3.connect(backup / "deal_finder-consistent.db")) as db:
        assert db.execute("SELECT value FROM sample").fetchall() == [("from-wal",)]


def test_shutdown_waits_for_service_before_closing_sqlite(monkeypatch):
    from scripts import launch_tray
    from offerte import db

    events = []
    monkeypatch.setitem(
        sys.modules,
        "offerte.agent_service",
        SimpleNamespace(shutdown_services=lambda **kw: events.append("service") or True),
    )
    monkeypatch.setattr(db, "close_all", lambda: events.append("database"))
    monkeypatch.setattr(launch_tray.os, "_exit", lambda code: events.append("exit"))
    launch_tray._spegni_ordinato()
    assert events == ["service", "database", "exit"]


def test_shutdown_keeps_sqlite_open_while_worker_is_active(monkeypatch):
    from scripts import launch_tray
    from offerte import db

    monkeypatch.setitem(
        sys.modules, "offerte.agent_service", SimpleNamespace(shutdown_services=lambda **kw: False)
    )
    monkeypatch.setattr(db, "close_all", lambda: pytest.fail("DB must remain open"))
    monkeypatch.setattr(launch_tray.os, "_exit", lambda code: pytest.fail("worker still running"))
    launch_tray._spegni_ordinato()


def test_update_stages_internal_runtime_and_restarts_root_launcher(tmp_path, monkeypatch):
    from offerte import update

    source, target = _organized(tmp_path / "source"), _organized(tmp_path / "target")
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for file in source.rglob("*"):
            if file.is_file():
                bundle.write(file, "DealFinder/" + file.relative_to(source).as_posix())

    class Response:
        content = archive.read_bytes()

        def json(self):
            return {
                "tag_name": "v9.0.0",
                "assets": [
                    {
                        "name": layout.ORGANIZED_ASSET,
                        "browser_download_url": "https://example.invalid/release.zip",
                    }
                ],
            }

    monkeypatch.setattr(update.sys, "executable", str(target / "app" / "DealFinder.exe"))
    monkeypatch.setattr(update.paths, "data_dir", lambda: target / "state")
    monkeypatch.setenv("DEAL_FINDER_LAUNCHER_PID", "1234")
    launches = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda cmd, **kwargs: launches.append(cmd))
    monkeypatch.setattr(
        update.threading, "Timer", lambda *args: SimpleNamespace(start=lambda: None)
    )
    assert update._installa_bundle(fetch=lambda *args: Response())["ok"]
    cmd = launches[0]
    assert cmd[cmd.index("--dest") + 1] == str(target)
    assert cmd[cmd.index("--exe") + 1] == str(target / "DealFinder.exe")
    assert cmd[cmd.index("--launcher-pid") + 1] == "1234"
    staged = Path(cmd[0]).parent
    assert (staged / "_internal" / "python311.dll").exists()


def test_organizer_excludes_private_state_and_keeps_distributed_assets(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in packaging.RUNTIME_FILES:
        (raw / name).write_text("runtime", encoding="utf-8")
    for relative in (
        "_internal/vinted_scraper/utils/agents.json",
        "_internal/offerte/mcp_server.py",
        "data/deal_finder.db",
        "state/local_secrets.json",
        ".streamlit/secrets.toml",
        ".giorno-notte/HANDOFF.md",
        ".env",
    ):
        file = raw / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("synthetic", encoding="utf-8")
    compiler = tmp_path / "compiler.exe"
    compiler.write_text("test compiler", encoding="utf-8")
    monkeypatch.setattr(packaging, "compiler_path", lambda: compiler)

    def compile_launcher(cmd, **kwargs):
        output = next(arg.removeprefix("/out:") for arg in cmd if arg.startswith("/out:"))
        Path(output).write_text("compiled launcher", encoding="utf-8")

    monkeypatch.setattr(packaging.subprocess, "run", compile_launcher)
    target = packaging.organize_bundle(raw, tmp_path / "package")
    assert {p.name for p in target.iterdir()} == packaging.PUBLIC_ROOT
    assert not list((target / "state").iterdir())
    assert (target / "app" / "_internal" / "vinted_scraper" / "utils" / "agents.json").exists()
    assert (target / "app" / "_internal" / "offerte" / "mcp_server.py").exists()
    assert not any(
        p.name in {"local_secrets.json", "deal_finder.db", "secrets.toml", "HANDOFF.md", ".env"}
        for p in target.rglob("*")
    )
    (raw / "personal.pdf").write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="estranei"):
        packaging.organize_bundle(raw, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_dual_archives_keep_historical_name_flat_and_new_name_organized(tmp_path):
    raw = tmp_path / "raw"
    (raw / "_internal").mkdir(parents=True)
    (raw / "_internal" / "python311.dll").write_text("runtime", encoding="utf-8")
    for name in packaging.RUNTIME_FILES:
        (raw / name).write_text("runtime", encoding="utf-8")
    (raw / "data").mkdir()
    (raw / "data" / "local_secrets.json").write_text("must not ship", encoding="utf-8")
    organized = _organized(tmp_path / "organized")
    assets = packaging.create_release_archives(raw, organized, tmp_path)
    assert set(assets) == {layout.LEGACY_ASSET, layout.ORGANIZED_ASSET}
    for name, expected in ((layout.LEGACY_ASSET, "legacy"), (layout.ORGANIZED_ASSET, "organized")):
        update_sync.valida_archivio(assets[name], expected_layout=expected)
        with zipfile.ZipFile(assets[name]) as archive:
            assert not any(n.endswith("local_secrets.json") for n in archive.namelist())
            assert ("DealFinder/Aggiorna.exe" in archive.namelist()) == (expected == "legacy")
            assert ("DealFinder/app/Aggiorna.exe" in archive.namelist()) == (
                expected == "organized"
            )
    with pytest.raises(ValueError, match="layout archivio diverso"):
        update_sync.valida_archivio(assets[layout.LEGACY_ASSET], expected_layout="organized")


@pytest.mark.parametrize("organized", [False, True])
def test_updater_selects_only_matching_layout_asset(tmp_path, monkeypatch, organized):
    from offerte import update

    target = _organized(tmp_path / "target") if organized else _legacy(tmp_path / "target")
    source = _organized(tmp_path / "source") if organized else _legacy(tmp_path / "source")
    if not organized:
        (source / "LEGGIMI.txt").write_text("readme", encoding="utf-8")
    archive = tmp_path / "payload.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for file in source.rglob("*"):
            if file.is_file():
                output.write(file, "DealFinder/" + file.relative_to(source).as_posix())
    selected = layout.ORGANIZED_ASSET if organized else layout.LEGACY_ASSET
    monkeypatch.setattr(
        update.sys, "executable", str(layout.runtime_dir(target) / "DealFinder.exe")
    )
    monkeypatch.setattr(update.paths, "data_dir", lambda: layout.state_dir(target))
    monkeypatch.setattr(update.subprocess, "Popen", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        update.threading, "Timer", lambda *args: SimpleNamespace(start=lambda: None)
    )
    urls = []

    class Response:
        content = archive.read_bytes()

        def json(self):
            return {
                "tag_name": "v4.3.0",
                "assets": [
                    {"name": name, "browser_download_url": "https://example.invalid/" + name}
                    for name in (layout.LEGACY_ASSET, layout.ORGANIZED_ASSET)
                ],
            }

    def fetch(url, headers):
        urls.append(url)
        return Response()

    assert update._installa_bundle(fetch=fetch)["ok"]
    assert urls == [update.GITHUB_API, "https://example.invalid/" + selected]


@pytest.mark.parametrize("wrong_content", [False, True])
def test_organized_update_never_falls_back_or_stops_for_wrong_asset(
    tmp_path, monkeypatch, wrong_content
):
    from offerte import update

    target = _organized(tmp_path / "target")
    flat = _legacy(tmp_path / "flat")
    (flat / "LEGGIMI.txt").write_text("readme", encoding="utf-8")
    archive = tmp_path / "flat.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for file in flat.rglob("*"):
            if file.is_file():
                output.write(file, "DealFinder/" + file.relative_to(flat).as_posix())
    monkeypatch.setattr(update.sys, "executable", str(target / "app" / "DealFinder.exe"))
    monkeypatch.setattr(update.paths, "data_dir", lambda: target / "state")
    monkeypatch.setattr(
        update.subprocess, "Popen", lambda *a, **kw: pytest.fail("Do not start updater")
    )
    monkeypatch.setattr(update.threading, "Timer", lambda *a, **kw: pytest.fail("Do not stop app"))

    class Response:
        content = archive.read_bytes()

        def json(self):
            name = layout.ORGANIZED_ASSET if wrong_content else layout.LEGACY_ASSET
            return {
                "assets": [
                    {"name": name, "browser_download_url": "https://example.invalid/bundle.zip"}
                ]
            }

    result = update._installa_bundle(fetch=lambda *a: Response())
    assert not result["ok"]
    assert not (target / "state" / "aggiornamento.lock").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows launcher")
def test_real_native_launcher_forwards_quoted_arguments(tmp_path):
    compiler = packaging.compiler_path()
    if not compiler.exists():
        pytest.skip(".NET Framework compiler not installed")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "_internal").mkdir()
    (raw / "_internal" / "runtime.dll").write_text("fake runtime", encoding="utf-8")
    child = tmp_path / "child.cs"
    child.write_text(
        'using System; using System.IO; static class Child { static int Main(string[] args) { File.WriteAllLines(Path.Combine(Environment.GetEnvironmentVariable("DEAL_FINDER_WORKSPACE"), "state", "args.txt"), args); return 7; } }',
        encoding="utf-8",
    )
    subprocess.run(
        [str(compiler), "/nologo", "/target:winexe", f"/out:{raw / 'DealFinder.exe'}", str(child)],
        check=True,
        capture_output=True,
    )
    for name in ("Aggiorna.exe", "DealFinderMCP.exe"):
        (raw / name).write_text("test helper", encoding="utf-8")
    target = packaging.organize_bundle(raw, tmp_path / "installed with spaces è")
    args = [
        "--no-browser",
        "--port",
        "9511",
        "spazio e ' apostrofo",
        'quoted"value',
        "path\\",
        "",
        "perché",
    ]
    result = subprocess.run(
        [str(target / "DealFinder.exe"), *args], cwd=tmp_path, timeout=20, capture_output=True
    )
    assert result.returncode == 7
    assert (target / "state" / "args.txt").read_text(encoding="utf-8-sig").splitlines() == args
