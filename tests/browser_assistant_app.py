"""Streamlit fixture: real UI and persistence, synthetic router and catalog only."""

import json
import runpy
from pathlib import Path
import _shared
from ui.settings import render_settings

from offerte.agent_service import get_service
from offerte.connection_routing import get_router
from offerte.models import Offerta


class SyntheticRouter:
    last_call = {"connection_id": "synthetic", "model": "fixture"}

    def complete(self, task, messages, *, output_schema=None):
        if task == "plan":
            names = ["iPhone 15", "notebook", "cuffie", "lavatrice", "monitor"]
            budgeted = "500" in messages[-1]["content"]
            return {
                "products": [
                    {
                        "query": name,
                        "label": name,
                        "budget_max": 500 if budgeted and index < 2 else None,
                    }
                    for index, name in enumerate(names)
                ],
                "clarification": "",
            }
        if task == "analysis":
            items = json.loads(messages[-1]["content"])
            return {
                "products": [
                    {
                        "product_id": item["product_id"],
                        "evaluations": [
                            {
                                "offer_id": offer["offer_id"],
                                "score": 8,
                                "reason": "Prezzo osservato e nome pertinente.",
                            }
                            for offer in item["offers"]
                        ],
                        "recommended_offer_ids": [item["offers"][0]["offer_id"]]
                        if item["offers"]
                        else [],
                        "summary": "Offerte sintetiche del test.",
                    }
                    for item in items
                ]
            }
        return "Confronto sintetico completato per tutti e cinque i prodotti. I prezzi osservati sono 100 e 150 euro."

    def cancel(self):
        pass

    def reset_cancel(self):
        pass


def synthetic_collect(**kwargs):
    from offerte import source_status

    source_status.reset()
    source_status.report_ok("amazon", 2)
    return [
        Offerta(
            kwargs["query"] + " 128GB",
            price,
            "Negozio test",
            f"https://example.com/{kwargs['query'].replace(' ', '-')}/{price}",
            "amazon",
        )
        for price in (100, 150)
    ]


service = get_service()
service._router = SyntheticRouter()
service._collector = synthetic_collect
# The selected profile controls only the initial screen. The fixture service
# above owns every completion, so this never starts a native or API runtime.
get_router().connections.use_codex_profile()
_shared._render_settings = render_settings
runpy.run_path(str(Path(__file__).resolve().parents[1] / "app.py"), run_name="__main__")
