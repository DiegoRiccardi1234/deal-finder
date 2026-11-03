"""Real persistence and bounded coordination, with no provider/network calls."""

from __future__ import annotations

import json
import threading
import time
from copy import deepcopy

import pytest

from offerte.agent_service import AgentService
from offerte.agent_tools import AgentToolError
from offerte.db import close_all, get_db
from offerte.models import Offerta


def wait(service, operation_id, expected=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        item = service.get_operation(operation_id)
        if item["status"] in (expected or {"completed", "partial", "cancelled", "failed"}):
            return item
        time.sleep(0.005)
    pytest.fail(f"operation did not settle: {service.get_operation(operation_id)}")


def evaluation(ticket):
    return {
        "products": [
            {
                "product_id": item["product_id"],
                "evaluations": [
                    {"offer_id": offer["offer_id"], "score": 8, "reason": "Dati presenti"}
                    for offer in item["offers"]
                ],
                "recommended_offer_ids": [
                    offer["offer_id"] for offer in item["offers"][:1] if not offer["avvisi"]
                ],
                "summary": "Risultati osservati" if item["offers"] else "Nessun risultato",
            }
            for item in ticket["products"]
        ]
    }


class Router:
    def __init__(self, plan):
        self.plan = plan
        self.calls = []
        self.cancelled = False

    def complete(self, task, messages, *, output_schema=None):
        self.calls.append(task)
        if task == "plan":
            return {"products": deepcopy(self.plan), "clarification": ""}
        if task == "analysis":
            items = json.loads(messages[-1]["content"])
            return evaluation({"products": items})
        return "Riepilogo completato sui risultati osservati."

    def cancel(self):
        self.cancelled = True

    def reset_cancel(self):
        self.cancelled = False


@pytest.fixture
def services(tmp_path):
    owners = []

    def create(**kwargs):
        service = AgentService(data_dir=tmp_path, **kwargs)
        owners.append(service)
        return service

    yield create
    for service in owners:
        service.shutdown()
    close_all()


def offers(**kwargs):
    assert kwargs["analysis_mode"] == "collect"
    assert kwargs["cerebras_client"] is None
    return [
        Offerta(kwargs["query"], 100, "Store", "https://example.com/" + kwargs["query"], "amazon")
    ]


def test_legacy_ui_lease_and_mcp_batch_share_one_search_slot(services):
    service = services(router=Router([]), collector=offers)
    with service.search_lease() as cancel:
        assert not cancel.is_set()
        with pytest.raises(AgentToolError, match="search_in_progress"):
            service.start_batch([{"query": "phone"}], "batch-during-ui")
        with pytest.raises(AgentToolError, match="search_in_progress"):
            with service.search_lease():
                pytest.fail("Second legacy search was allowed")
        # Publishing UI results does not reacquire or bypass the search slot.
        assert (
            service.record_search(
                {"query": "phone"},
                offers(query="phone", analysis_mode="collect", cerebras_client=None),
            )["status"]
            == "completed"
        )
    operation = service.start_batch([{"query": "phone"}], "after-ui")
    assert wait(service, operation["id"])["status"] == "completed"


def test_shutdown_cancels_legacy_ui_lease_and_waits_for_its_exit(services):
    service = services(router=Router([]), collector=offers)
    with service.search_lease() as cancel:
        assert service.shutdown(timeout=0) is False
        assert cancel.is_set()
        assert not service._owner_released
    assert service.shutdown(timeout=0) is True
    assert service._owner_released


def test_batch_uses_own_ebay_credentials_without_exporting_them_or_mutating_env(
    services, monkeypatch
):
    monkeypatch.setenv("EBAY_APP_ID", "environment-app-fixture")
    monkeypatch.setenv("EBAY_CERT_ID", "environment-cert-fixture")
    captured = []
    service = services(router=Router([]), collector=lambda **kw: captured.append(kw) or [])
    (service.data_dir / "local_secrets.json").write_text(
        json.dumps(
            {"EBAY_APP_ID": "workspace-app-fixture", "EBAY_CERT_ID": "workspace-cert-fixture"}
        ),
        encoding="utf-8",
    )
    operation = service.start_batch([{"query": "phone"}], "ebay-private")
    result = wait(service, operation["id"])
    assert result["status"] == "completed"
    assert captured[0]["app_id"] == "workspace-app-fixture"
    assert captured[0]["cert_id"] == "workspace-cert-fixture"
    assert "workspace-app-fixture" not in json.dumps(result)
    assert "workspace-cert-fixture" not in json.dumps(result)
    import os

    assert os.environ["EBAY_APP_ID"] == "environment-app-fixture"
    assert os.environ["EBAY_CERT_ID"] == "environment-cert-fixture"


def test_batch_corrupt_secret_store_fails_closed_before_network_collection(services, monkeypatch):
    monkeypatch.setenv("EBAY_CERT_ID", "must-not-use-env-after-corruption")
    captured = []
    service = services(router=Router([]), collector=lambda **kw: captured.append(kw) or [])
    (service.data_dir / "local_secrets.json").write_text("{broken", encoding="utf-8")
    operation = service.start_batch([{"query": "phone"}], "ebay-corrupt")
    result = wait(service, operation["id"])
    assert result["status"] == "partial" and result["products"][0]["status"] == "failed"
    assert captured == []
    assert "must-not-use" not in json.dumps(result)


def test_batch_five_products_is_sequential_scoped_and_never_uses_router(services):
    router = Router([])
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        if kwargs["query"] == "empty":
            return []
        if kwargs["query"] == "failure":
            raise RuntimeError("do not expose raw data")
        return offers(**kwargs)

    service = services(router=router, collector=collect)
    selected = [
        {"query": name, "budget_max": budget}
        for name, budget in [
            ("phone", 900),
            ("headphones", 200),
            ("empty", None),
            ("failure", 100),
            ("laptop", 1500),
        ]
    ]
    started = service.start_batch(selected, "five")
    settled = wait(service, started["operation_id"])
    assert settled["status"] == "partial"
    assert [call["query"] for call in calls] == [item["query"] for item in selected]
    assert [call["budget_max"] for call in calls] == [900, 200, None, 100, 1500]
    assert [item["status"] for item in settled["products"]] == [
        "completed",
        "completed",
        "empty",
        "failed",
        "completed",
    ]
    assert router.calls == []
    assert service.start_batch(selected, "five")["id"] == started["id"]
    assert len(calls) == 5
    with pytest.raises(AgentToolError, match="request_id_conflict"):
        service.start_batch([{"query": "different"}], "five")
    assert service.list_offers(started["id"], "p5")["items"][0]["nome"] == "laptop"


def test_budget_is_asked_once_and_unknown_answer_keeps_null(services):
    router = Router([{"query": "phone", "budget_min": 650, "budget_max": 900}])
    seen = []
    service = services(router=router, collector=lambda **kw: seen.append(kw) or offers(**kw))
    started = service.start_request("Cercami un telefono 128GB", "budget")
    waiting = wait(service, started["id"], {"awaiting_budget", "failed"})
    assert waiting["status"] == "awaiting_budget"
    assert seen == []
    service.answer_request(started["id"], "non so")
    assert wait(service, started["id"])["status"] == "completed"
    assert seen[0]["budget_max"] is None
    assert seen[0]["prezzo_min"] == 0
    assert router.calls == ["plan", "analysis", "advice"]
    assert service.answer_request(started["id"], "non so")["status"] == "completed"


@pytest.mark.parametrize("structured_advice", [False, True])
def test_final_advice_is_readable_without_an_extra_inference(services, structured_advice):
    class AdviceRouter(Router):
        def complete(self, task, messages, *, output_schema=None):
            if task != "advice":
                return super().complete(task, messages, output_schema=output_schema)
            self.calls.append(task)
            assert output_schema is None
            assert "mai JSON" in messages[0]["content"]
            assert "evaluations" not in messages[0]["content"]
            if structured_advice:
                return json.dumps(json.loads(messages[-1]["content"])["analysis"])
            return "Consiglio leggibile sulle offerte osservate."

    router = AdviceRouter([{"query": "phone", "budget_max": 500}])
    service = services(router=router, collector=offers)
    started = service.start_request("Cercami un telefono massimo 500 euro", "readable-advice")
    settled = wait(service, started["id"])
    assert settled["status"] == "completed"
    assert router.calls == ["plan", "analysis", "advice"]
    if structured_advice:
        assert settled["advice"] == "**phone**\n\nRisultati osservati"
    else:
        assert settled["advice"] == "Consiglio leggibile sulle offerte osservate."
    assert '"evaluations"' not in settled["advice"]


def test_cancel_preserves_first_product_and_skips_later_products(services):
    started_second, release = threading.Event(), threading.Event()
    calls = []

    def collect(**kw):
        calls.append(kw["query"])
        if len(calls) == 2:
            started_second.set()
            release.wait(3)
        return offers(**kw)

    service = services(collector=collect)
    started = service.start_batch([{"query": str(index)} for index in range(5)], "cancel")
    assert started_second.wait(3)
    service.cancel_operation(started["id"])
    release.set()
    settled = wait(service, started["id"])
    assert settled["status"] == "cancelled"
    assert calls == ["0", "1"]
    assert service.list_offers(started["id"], "p1")["total"] == 1
    assert all(item["status"] == "cancelled" for item in settled["products"][1:])


def test_ticket_validates_entire_batch_and_provenance_before_commit(services):
    service = services(collector=offers)
    operation = service.start_batch([{"query": "a"}, {"query": "b"}], "analysis")
    wait(service, operation["id"])
    ticket = service.prepare_analysis(operation["id"])
    valid = evaluation(ticket)
    invalid = deepcopy(valid)
    invalid["products"][1]["evaluations"][0]["offer_id"] = "p1:1"
    with pytest.raises(AgentToolError, match="invalid_offer_analysis"):
        service.submit_analysis(ticket["ticket_id"], invalid, "Codex")
    assert service.get_operation(ticket["ticket_id"])["status"] == "pending"
    saved = service.submit_analysis(ticket["ticket_id"], valid, "Codex")
    assert saved["source"] == "external/Codex"
    assert service.submit_analysis(ticket["ticket_id"], valid, "Codex") == saved
    with pytest.raises(AgentToolError, match="analysis_already_submitted"):
        service.submit_analysis(ticket["ticket_id"], valid, "different")


def test_stale_snapshot_and_expired_ticket_are_rejected(services):
    service = services(collector=offers)
    operation = service.start_batch([{"query": "a"}], "stale")
    wait(service, operation["id"])
    ticket = service.prepare_analysis(operation["id"])
    row = service._row(operation["id"])
    result = json.loads(row["result_json"])
    result["products"][0]["offers"][0]["prezzo"] = 999
    service._update(operation["id"], "completed", result=result)
    with pytest.raises(AgentToolError, match="snapshot_changed"):
        service.submit_analysis(ticket["ticket_id"], evaluation(ticket), "external")
    ticket = service.prepare_analysis(operation["id"])
    payload = json.loads(service._row(ticket["ticket_id"])["payload_json"])
    payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    service._update(ticket["ticket_id"], "pending", payload=payload)
    with pytest.raises(AgentToolError, match="analysis_ticket_expired"):
        service.submit_analysis(ticket["ticket_id"], evaluation(ticket), "external")


def test_watchlist_desired_state_idempotence_and_no_sensitive_dispatch(services):
    service = services(collector=offers)
    operation = service.start_batch([{"query": "a"}], "watch-search")
    wait(service, operation["id"])
    first = service.set_watchlist(operation["id"], "p1:1", True, "favorite")
    assert service.set_watchlist(operation["id"], "p1:1", True, "favorite") == first
    assert service.list_watchlist()["total"] == 1
    service.set_watchlist(operation["id"], "p1:1", False, "remove")
    assert service.list_watchlist()["total"] == 0
    for name in (
        "dealfinder_setup",
        "dealfinder_confirm",
        "dealfinder_start_request",
        "dealfinder_delete_database",
    ):
        with pytest.raises(AgentToolError, match="unknown_tool"):
            service.dispatch(name, {})
    with pytest.raises(AgentToolError, match="invalid_tool_arguments"):
        service.dispatch(
            "dealfinder_start_batch_search",
            {"selected": [{"query": "x"}], "request_id": "x", "external": False},
        )


def test_additive_migration_and_ui_snapshot_preserve_old_user_data(services, tmp_path):
    db = get_db(str(tmp_path / "deal_finder.db"))
    db.execute(
        "INSERT INTO watchlist(link,nome,prezzo,fonte,timestamp) VALUES (?,?,?,?,?)",
        ("https://example.com/old", "old", 10, "store", "before"),
    )
    service = services()
    recorded = service.record_search(
        {
            "query": "manual",
            "prezzo_min": 0,
            "budget_max": 500,
            "condizione": "nuovo",
            "fonti_backend": ["amazon"],
        },
        [Offerta("Phone", 100, "Store", "https://example.com/phone")],
    )
    assert service.list_offers(recorded["id"])["total"] == 1
    assert service.list_watchlist()["items"][0]["nome"] == "old"
    assert service.list_searches()["items"][0]["kind"] == "ui_search"


def test_restart_does_not_replay_or_resume_inference(services):
    service = services()
    operation_id = service._insert("request", {"text": "private"}, "planning")
    service.shutdown()
    router = Router([])
    restarted = services(router=router)
    assert restarted.get_operation(operation_id)["status"] == "interrupted"
    assert router.calls == []
