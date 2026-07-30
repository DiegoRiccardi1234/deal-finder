"""Auto-update per installazioni locali.

All'avvio l'app può controllare se su GitHub esiste una release più recente e,
con un click, eseguire `git pull` + reinstallo dipendenze. Per chi ha scaricato
lo ZIP del release (niente `.git`) viene mostrato il link al nuovo release.

Su Streamlit Cloud l'update è disabilitato (la piattaforma si aggiorna da sola).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable

from offerte.config import VERSION

_REPO = "DiegoRiccardi1234/deal-finder"
GITHUB_API = f"https://api.github.com/repos/{_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{_REPO}/releases/latest"


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def current_version() -> str:
    return VERSION


def _parse_version(s: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", str(s or ""))
    return tuple(int(n) for n in nums) if nums else (0,)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _default_fetch(url: str, headers: dict):
    from offerte.http import fetch_with_retry, get_headers

    h = get_headers()
    h.update(headers)
    return fetch_with_retry(url, h)


def latest_release(*, fetch: Callable | None = None) -> str | None:
    """Tag dell'ultima release GitHub (es. 'v1.2.0'), o None su errore."""
    fetch = fetch or _default_fetch
    try:
        resp = fetch(GITHUB_API, {"Accept": "application/vnd.github+json"})
        tag = (resp.json() or {}).get("tag_name")
        return str(tag) if tag else None
    except Exception:
        return None


def update_available(*, fetch: Callable | None = None, current: str | None = None) -> str | None:
    """Ritorna il tag remoto se più recente della versione locale, altrimenti None."""
    current = current or current_version()
    latest = latest_release(fetch=fetch)
    if latest and is_newer(latest, current):
        return latest
    return None


def is_git_clone(root: str | None = None) -> bool:
    return os.path.isdir(os.path.join(root or _project_root(), ".git"))


def is_cloud() -> bool:
    """True su Streamlit Cloud o se l'update è disabilitato via env."""
    if os.environ.get("DISABLE_AUTO_UPDATE", "").strip() == "1":
        return True
    return os.path.isdir("/mount/src")


def _default_runner(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def do_update(*, root: str | None = None, runner: Callable | None = None) -> dict:
    """Aggiorna l'installazione. Git clone → git pull + reinstall; ZIP → link release."""
    root = root or _project_root()
    if not is_git_clone(root):
        return {
            "ok": False,
            "mode": "zip",
            "url": RELEASES_URL,
            "message": "Installazione senza git: scarica l'ultima release dal link.",
        }
    runner = runner or _default_runner
    try:
        out1 = runner(["git", "-C", root, "pull", "--ff-only"])
        out2 = runner(
            [sys.executable, "-m", "pip", "install", "-r", os.path.join(root, "requirements.txt")]
        )
        return {"ok": True, "mode": "git", "log": f"{out1}\n{out2}".strip()}
    except Exception as e:
        return {"ok": False, "mode": "git", "error": str(e)}
