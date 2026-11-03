"""A coherent JSON plan cannot silently move money or invent a requested model."""

from offerte.agent_tools import ground_budgets, grounded_products, products


def test_money_bounds_cannot_be_swapped_between_products():
    plan = products(
        [{"query": "iPhone 15", "budget_max": 700}, {"query": "lavatrice", "budget_max": 500}]
    )
    ground_budgets(plan, "iPhone15 massimo 500 euro; lavatrice massimo 700 euro")
    assert [item["budget_max"] for item in plan] == [None, None]


def test_distinct_budgets_and_unknown_are_product_scoped():
    plan = products(
        [
            {"query": "iPhone 15", "budget_max": 500},
            {"query": "lavatrice", "budget_max": 700},
            {"query": "cuffie", "budget_max": None, "budget_unlimited": True},
        ]
    )
    ground_budgets(plan, "iPhone15 massimo 500 euro; lavatrice massimo 700 euro; cuffie non so")
    assert [item["budget_max"] for item in plan] == [500, 700, None]
    assert plan[2]["budget_unlimited"]


def test_hallucinated_unlimited_flag_does_not_skip_question():
    plan = products([{"query": "cuffie", "budget_unlimited": True}])
    ground_budgets(plan, "Cercami delle cuffie")
    assert not plan[0]["budget_unlimited"]


def test_product_scope_accepts_synonyms_not_unrequested_models():
    assert grounded_products(products([{"query": "phone"}]), "Cercami un telefono")
    assert grounded_products(products([{"query": "iPhone 15"}]), "Cercami iphone15")
    assert not grounded_products(products([{"query": "PlayStation 5"}]), "Cercami iPhone15")
    assert not grounded_products(products([{"query": "iPhone 16"}]), "Cercami iPhone15")
    assert not grounded_products(products([{"query": "iPhone 15"}]), "Cercami un telefono")


def test_explicit_shared_budget_is_allowed():
    plan = products(
        [{"query": "cuffie", "budget_max": 500}, {"query": "monitor", "budget_max": 500}]
    )
    ground_budgets(plan, "Cuffie e monitor, massimo 500 euro ciascuno")
    assert [item["budget_max"] for item in plan] == [500, 500]


def test_legacy_intent_uses_plan_route_not_analysis_or_api(monkeypatch):
    from offerte import ai
    from offerte import connection_routing

    tasks = []

    class Router:
        def complete(self, task, messages):
            tasks.append(task)
            return {
                "query": "iPhone 15",
                "prezzo_min": 0,
                "prezzo_max": 500,
                "condizione": "tutti",
                "filtri": {},
            }

    client = connection_routing.RoutedClient(Router(), "plan")
    monkeypatch.setattr(
        connection_routing, "routed_client", lambda task: client if task == "plan" else None
    )
    monkeypatch.setattr(
        ai,
        "_get_cerebras_client",
        lambda: (_ for _ in ()).throw(AssertionError("Legacy API must not be resolved")),
    )
    result = ai.parse_search_intent("iPhone 15 massimo 500 euro")
    assert result["query"] == "iPhone 15"
    assert tasks == ["plan"]
