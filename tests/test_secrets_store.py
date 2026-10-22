"""Le chiavi salvate dal pannello: precedenza, cancellazione, e cosa non esce."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from offerte import secrets_store

    monkeypatch.setattr(secrets_store.paths, "data_dir", lambda: tmp_path)
    # Le chiavi vere di chi sviluppa stanno nell'ambiente: senza ripulirlo un
    # test che verifica «non configurato» passerebbe o fallirebbe a seconda del
    # computer su cui gira.
    for campo in secrets_store.CAMPI_AMMESSI:
        monkeypatch.delenv(campo, raising=False)
    return secrets_store


def test_salva_e_rilegge(store) -> None:
    assert store.salva("GEMINI_API_KEY", "  sk-123  ") is True
    assert store.carica()["GEMINI_API_KEY"] == "sk-123", "lo spazio va tolto"


def test_stringa_vuota_cancella(store) -> None:
    import os

    store.salva("GROQ_API_KEY", "gk-1")
    store.salva("GROQ_API_KEY", "")
    assert "GROQ_API_KEY" not in store.carica()
    assert "GROQ_API_KEY" not in os.environ


def test_rimuovere_la_chiave_del_pannello_ripristina_l_origine_esterna(store, monkeypatch):
    import os

    monkeypatch.setenv("GEMINI_API_KEY", "external-original")
    store.salva("GEMINI_API_KEY", "panel-override")
    store.salva("GEMINI_API_KEY", "replacement-override")
    store.salva("GEMINI_API_KEY", "")
    assert os.environ["GEMINI_API_KEY"] == "external-original"
    assert store.riepilogo_provider()["gemini"]["origine"] == "ambiente"


def test_cancellazione_non_tocca_un_nuovo_valore_esterno(store, monkeypatch):
    import os

    store.salva("GEMINI_API_KEY", "panel")
    monkeypatch.setenv("GEMINI_API_KEY", "changed-externally")
    store.salva("GEMINI_API_KEY", "")
    assert os.environ["GEMINI_API_KEY"] == "changed-externally"


def test_salvare_un_campo_preserva_i_legacy_senza_applicarli(store, monkeypatch):
    import os

    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    store.percorso().write_text(
        json.dumps({"CEREBRAS_API_KEY": "legacy-private-key", "AI_PROVIDER": "cerebras"}),
        encoding="utf-8",
    )
    store.salva("GEMINI_API_KEY", "new-key")
    raw = json.loads(store.percorso().read_text(encoding="utf-8"))
    assert raw["CEREBRAS_API_KEY"] == "legacy-private-key"
    assert raw["AI_PROVIDER"] == "cerebras"
    assert "CEREBRAS_API_KEY" not in store.carica()
    assert "CEREBRAS_API_KEY" not in os.environ


def test_un_campo_non_ammesso_non_diventa_una_variabile(store, monkeypatch) -> None:
    """Il pannello scrive su disco e da lì nell'ambiente: una lista aperta
    vorrebbe dire che un nome arbitrario diventa una variabile d'ambiente."""
    assert store.salva("PATH", "/qualcosa/di/brutto") is False
    assert not store.percorso().exists()


def test_lo_stato_non_restituisce_mai_la_chiave(store) -> None:
    store.salva("OPENAI_API_KEY", "sk-segretissima")
    stato = store.status()
    assert stato["OPENAI_API_KEY"] is True
    assert "sk-segretissima" not in json.dumps(stato)


def test_il_pannello_scavalca_l_ambiente(store, monkeypatch) -> None:
    """Se l'utente ha appena scritto una chiave, un `.env` dimenticato non deve
    vincere: è un difetto che a schermo non si spiega."""
    import os

    monkeypatch.setenv("ANTHROPIC_API_KEY", "quella-vecchia-del-env")
    store.salva("ANTHROPIC_API_KEY", "quella-nuova-del-pannello")
    assert os.environ["ANTHROPIC_API_KEY"] == "quella-nuova-del-pannello"


def test_un_file_illeggibile_non_rompe_l_avvio(store) -> None:
    store.percorso().write_text("{ questo non e' json", encoding="utf-8")
    assert store.carica() == {}


def test_riepilogo_distingue_pannello_da_ambiente(store, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "dall-ambiente")
    store.salva("GEMINI_API_KEY", "dal-pannello")

    riepilogo = store.riepilogo_provider()

    assert riepilogo["gemini"]["origine"] == "pannello"
    assert riepilogo["groq"]["origine"] == "ambiente"
    assert riepilogo["openai"]["configurato"] is False
