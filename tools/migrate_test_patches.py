"""
migrate_test_patches.py — Aggiorna i monkeypatch in tests/test_suite.py
dopo lo split di offerte_tech in package offerte/.

Per ogni test function, applica una sostituzione contestuale di
`offerte_tech.X` → modulo target appropriato.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tests" / "test_suite.py"


def get_function_ranges(text: str) -> list[tuple[str, int, int]]:
    """Lista (name, start_line, end_line_exclusive) per top-level functions."""
    tree = ast.parse(text)
    out: list[tuple[str, int, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = (node.end_lineno or node.lineno) + 1
            out.append((node.name, node.lineno, end))
    return out


def target_for(fn_name: str) -> str:
    """Restituisce il modulo target per le sostituzioni in questo test."""
    n = fn_name.lower()
    if "amazon" in n:
        return "offerte.scrapers.amazon"
    if "vinted" in n:
        return "offerte.scrapers.vinted"
    if "wallapop" in n:
        return "offerte.scrapers.wallapop"
    if "subito" in n:
        return "offerte.scrapers.subito"
    if "comet" in n:
        return "offerte.scrapers.comet"
    if "expert" in n:
        return "offerte.scrapers.expert"
    if "euronics" in n:
        return "offerte.scrapers.euronics"
    if "unieuro" in n:
        return "offerte.scrapers.unieuro"
    if "mediaworld" in n:
        return "offerte.scrapers.mediaworld"
    # cerca / nuove fonti / prezzo / spec / filtra: usano cerca_offerte → orchestrator
    return "offerte.orchestrator"


# Pattern per i monkeypatch.setattr("offerte_tech.X", ...)
PATCH_RE = re.compile(r'(["\'])offerte_tech\.([A-Za-z_][\w.]*)\1')


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    ranges = get_function_ranges(text)

    # Mappa: numero linea → target module per la funzione che la contiene
    line_target: dict[int, str] = {}
    for name, s, e in ranges:
        tgt = target_for(name)
        for ln in range(s, e):
            line_target[ln] = tgt

    # Anche il helper _make_monkeypatch_cerca che è usato dai test cerca_offerte
    # → orchestrator (già coperto da default fallback in target_for).

    new_lines: list[str] = []
    changes = 0
    for idx, line in enumerate(lines, start=1):
        tgt = line_target.get(idx)
        if tgt is None:
            new_lines.append(line)
            continue

        def sub(match: re.Match) -> str:
            nonlocal changes
            quote = match.group(1)
            attr = match.group(2)
            # Casi speciali: _get_cerebras_client esiste in offerte.ai
            if attr.startswith("_get_cerebras_client"):
                changes += 1
                return f'{quote}offerte.ai.{attr}{quote}'
            # _get_cerebras_api_key idem
            if attr.startswith("_get_cerebras_api_key"):
                changes += 1
                return f'{quote}offerte.ai.{attr}{quote}'
            changes += 1
            return f'{quote}{tgt}.{attr}{quote}'

        new_line = PATCH_RE.sub(sub, line)
        new_lines.append(new_line)

    new_text = "".join(new_lines)
    SRC.write_text(new_text, encoding="utf-8")
    print(f"Patched {changes} occurrences in tests/test_suite.py")


if __name__ == "__main__":
    main()
