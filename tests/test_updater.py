"""Test per l'auto-update (updater.py)."""

from __future__ import annotations

import os

import pytest


def test_parse_version_strips_v_prefix():
    import updater

    assert updater._parse_version("v1.2.3") == (1, 2, 3)
    assert updater._parse_version("1.0") == (1, 0)
    assert updater._parse_version("garbage") == (0,)


def test_is_newer_semver():
    import updater

    assert updater.is_newer("v1.2.0", "1.1.0") is True
    assert updater.is_newer("1.1.0", "1.1.0") is False
    assert updater.is_newer("1.0.0", "1.1.0") is False
    assert updater.is_newer("v2.0.0", "1.9.9") is True


def test_latest_release_parses_tag():
    import updater

    class _Resp:
        def json(self):
            return {"tag_name": "v1.4.0"}

    assert updater.latest_release(fetch=lambda url, headers: _Resp()) == "v1.4.0"


def test_latest_release_none_on_error():
    import updater

    def _boom(url, headers):
        raise RuntimeError("network down")

    assert updater.latest_release(fetch=_boom) is None


def test_update_available_true_when_remote_newer():
    import updater

    fetch = lambda url, headers: type("R", (), {"json": lambda self: {"tag_name": "v9.9.9"}})()
    assert updater.update_available(fetch=fetch, current="1.1.0") == "v9.9.9"


def test_update_available_none_when_up_to_date():
    import updater

    fetch = lambda url, headers: type("R", (), {"json": lambda self: {"tag_name": "v1.0.0"}})()
    assert updater.update_available(fetch=fetch, current="1.1.0") is None


def test_is_git_clone(tmp_path):
    import updater

    assert updater.is_git_clone(str(tmp_path)) is False
    os.makedirs(os.path.join(tmp_path, ".git"))
    assert updater.is_git_clone(str(tmp_path)) is True


def test_is_cloud_respects_disable_env(monkeypatch):
    import updater

    monkeypatch.setenv("DISABLE_AUTO_UPDATE", "1")
    assert updater.is_cloud() is True


def test_do_update_zip_returns_release_link(tmp_path):
    import updater

    # nessuna .git → modalità zip, ritorna il link al release
    result = updater.do_update(root=str(tmp_path))
    assert result["ok"] is False
    assert result["mode"] == "zip"
    assert "releases" in result["url"]


def test_do_update_git_runs_pull_and_install(tmp_path):
    import updater

    os.makedirs(os.path.join(tmp_path, ".git"))
    calls = []

    def _runner(cmd):
        calls.append(cmd)
        return "ok"

    result = updater.do_update(root=str(tmp_path), runner=_runner)
    assert result["ok"] is True
    assert result["mode"] == "git"
    joined = " ".join(" ".join(c) for c in calls)
    assert "pull" in joined and "pip" in joined and "install" in joined
