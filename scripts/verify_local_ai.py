"""Explicit subscription smoke on synthetic offers, never on personal shopping data.

Usage: python scripts/verify_local_ai.py --workspace <private empty folder> --live
Without --live this only reports native installation/authentication status.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from offerte.agent_runtime import get_runtime_status

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Save native errors privately for this synthetic test only.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Uses account allowance for one five-product synthetic workflow.",
    )
    args = parser.parse_args()
    status = get_runtime_status("codex")
    print(json.dumps({"native_status": status}, ensure_ascii=False), flush=True)
    if not args.live:
        return 0 if status["authenticated"] else 2
    if not args.workspace or not status["authenticated"]:
        parser.error("--workspace and native ChatGPT authentication are required for --live")
    directory = args.workspace.resolve()
    if directory.exists() and any(directory.iterdir()):
        parser.error("Use a new empty private workspace, not an existing installation")
    directory.mkdir(parents=True, exist_ok=True)
    if args.diagnostic:
        from offerte import agent_runtime

        original_error = agent_runtime._safe_error

        def capture_error(value):
            (directory / "native-error.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return original_error(value)

        agent_runtime._safe_error = capture_error
    from offerte.agent_service import AgentService
    from offerte.connection_routing import ConnectionRouter
    from offerte.db import close_all
    from offerte.models import Offerta

    seen = []

    def collect(**kwargs):
        from offerte import source_status

        assert kwargs["analysis_mode"] == "collect"
        source_status.reset()
        source_status.report_ok("amazon", 2)
        seen.append({"query": kwargs["query"], "budget_max": kwargs["budget_max"]})
        return [
            Offerta(
                kwargs["query"],
                price,
                "Synthetic shop",
                f"https://example.com/synthetic/{len(seen)}/{price}",
                "amazon",
            )
            for price in (100, 150)
        ]

    router = ConnectionRouter(directory)
    router.connections.use_codex_profile()
    service = AgentService(router=router, data_dir=directory, collector=collect)
    try:
        operation = service.start_request(
            "Prova sintetica: cercami iPhone 15 massimo 500 euro; notebook massimo 900 euro; "
            "cuffie senza tetto; lavatrice massimo 700 euro; monitor massimo 300 euro. "
            "Non aggiungere altri prodotti e usa soltanto i dati sintetici che l'app ti fornirà.",
            "native-synthetic-five-products",
        )
        deadline, previous = time.monotonic() + 950, None
        while time.monotonic() < deadline:
            operation = service.get_operation(operation["id"])
            marker = (operation["status"], operation.get("completed_products", 0))
            if marker != previous:
                print(
                    json.dumps(
                        {"status": marker[0], "completed_products": marker[1]}, ensure_ascii=False
                    ),
                    flush=True,
                )
                previous = marker
            if operation["status"] in {
                "completed",
                "partial",
                "failed",
                "cancelled",
                "awaiting_budget",
                "awaiting_clarification",
            }:
                break
            time.sleep(0.2)
        report = {
            "operation": operation,
            "collections": seen,
            "last_call": router.last_call,
            "success": operation["status"] == "completed"
            and len(seen) == 5
            and bool(operation.get("advice")),
        }
        (directory / "smoke-result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "success": report["success"],
                    "status": operation["status"],
                    "collections": seen,
                    "report": str(directory / "smoke-result.json"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if report["success"] else 1
    finally:
        service.shutdown()
        router.close()
        close_all()


if __name__ == "__main__":
    raise SystemExit(main())
