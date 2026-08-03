"""Ranking dei modelli: euristica sul nome, penalità imparate, salute live."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pulito():
    from offerte import endpoint_health, model_selector

    model_selector.dimentica_penalita()
    endpoint_health.svuota_cache()
    yield
    model_selector.dimentica_penalita()
    endpoint_health.svuota_cache()


def test_sotto_il_quality_floor_perde() -> None:
    from offerte.model_selector import qualita_dal_nome

    assert qualita_dal_nome("gemma-3-27b-it") > qualita_dal_nome("llama-3.2-3b-instruct")


def test_su_json_un_reasoning_model_e_una_penalita() -> None:
    """Bruciano il budget in catena di pensiero e troncano il JSON: sul compito
    strutturato la taglia non compensa."""
    from offerte.model_selector import qualita_dal_nome

    assert qualita_dal_nome("qwq-32b", compito="json") < qualita_dal_nome(
        "qwen-2.5-32b-instruct", compito="json"
    )


def test_la_taglia_non_cresce_all_infinito() -> None:
    """Un 26B pulito non deve essere scavalcato da un gigante solo per la taglia."""
    from offerte.model_selector import qualita_dal_nome

    assert qualita_dal_nome("modello-400b") == qualita_dal_nome("modello-40b")


def test_chi_tronca_scende_in_classifica() -> None:
    from offerte import model_selector

    modelli = ["alpha-70b-instruct", "beta-70b-instruct"]
    prima = model_selector.ordina(modelli, provider="openrouter")
    model_selector.registra_penalita("openrouter", prima[0], "troncato")
    dopo = model_selector.ordina(modelli, provider="openrouter")
    assert dopo[0] != prima[0]


def test_la_penalita_e_per_coppia_provider_modello() -> None:
    """Lo stesso slug può troncare su un host e andare su un altro."""
    from offerte import model_selector

    model_selector.registra_penalita("openrouter", "gpt-oss-120b", "troncato")
    assert model_selector.penalita_di("openrouter", "gpt-oss-120b") > 0
    assert model_selector.penalita_di("cerebras", "gpt-oss-120b") == 0


def test_il_429_scade_prima_delle_altre_penalita() -> None:
    """Su free tier è un throttle condiviso: tenerlo mezz'ora spegnerebbe
    modelli sani per un burst di qualcun altro."""
    from offerte import model_selector

    model_selector.registra_penalita("openrouter", "m", "rate_limit", now=0.0)
    model_selector.registra_penalita("openrouter", "n", "troncato", now=0.0)
    dopo = model_selector.TTL_RATE_LIMIT + 1
    assert model_selector.penalita_di("openrouter", "m", now=dopo) == 0
    assert model_selector.penalita_di("openrouter", "n", now=dopo) > 0


def test_un_modello_morto_non_viene_scelto() -> None:
    from offerte import endpoint_health, model_selector

    salute = {
        "morto-70b-instruct": endpoint_health.Health("morto-70b-instruct", False),
        "vivo-30b-instruct": endpoint_health.Health("vivo-30b-instruct", True, uptime_5m=99.0),
    }
    ordinati = model_selector.ordina(list(salute), provider="openrouter", salute=salute)
    assert ordinati[0] == "vivo-30b-instruct"


def test_nella_stessa_fascia_vince_la_qualita_non_il_decimale() -> None:
    """Due modelli entro il 2% di uptime sono equivalenti nei fatti."""
    from offerte import endpoint_health, model_selector

    salute = {
        "scarso-3b": endpoint_health.Health("scarso-3b", True, uptime_5m=100.0),
        "buono-32b-instruct": endpoint_health.Health("buono-32b-instruct", True, uptime_5m=99.0),
    }
    ordinati = model_selector.ordina(list(salute), provider="openrouter", salute=salute)
    assert ordinati[0] == "buono-32b-instruct"


def test_endpoints_vuoti_significa_modello_morto() -> None:
    from offerte import endpoint_health

    class _R:
        @staticmethod
        def json():
            return {"data": {"endpoints": []}}

    salute = endpoint_health.check("qualsiasi", fetch=lambda url: _R())
    assert salute.alive is False


def test_una_verifica_fallita_non_boccia_il_modello() -> None:
    """Nessuna informazione non è una bocciatura: decide il failover a valle."""
    from offerte import endpoint_health

    def esplode(url):
        raise OSError("rete giù")

    assert endpoint_health.check("qualsiasi", fetch=esplode).alive is True


def test_uptime_basso_esclude() -> None:
    from offerte import endpoint_health

    class _R:
        @staticmethod
        def json():
            return {"data": {"endpoints": [{"status": 0, "uptime_last_5m": 40.0}]}}

    assert endpoint_health.check("fiacco", fetch=lambda url: _R()).alive is False


def test_un_modello_dato_per_morto_finisce_in_coda_non_fuori() -> None:
    """Toglierlo sembrava più pulito e non lo è.

    La verifica può sbagliarsi — basta uno slug che non è di OpenRouter e la
    richiesta fallisce — e un candidato perso qui è un candidato che il failover
    non può più raggiungere.
    """
    from offerte import endpoint_health, model_selector

    salute = {
        "morto-70b-instruct": endpoint_health.Health("morto-70b-instruct", False),
        "vivo-30b-instruct": endpoint_health.Health("vivo-30b-instruct", True, uptime_5m=99.0),
    }
    ordinati = model_selector.ordina(list(salute), provider="openrouter", salute=salute)

    assert ordinati == ["vivo-30b-instruct", "morto-70b-instruct"]
    assert len(ordinati) == 2, "nessun candidato deve sparire dalla lista"
