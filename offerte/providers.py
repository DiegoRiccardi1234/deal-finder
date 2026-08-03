"""Astrazione multi-provider per i modelli AI.

Obiettivo: poter scegliere il backend LLM (Cerebras, Groq, OpenAI, OpenRouter,
Anthropic, Google Gemini) senza toccare i decine di call-site che usano la forma
OpenAI `client.chat.completions.create(...).choices[0].message.content`.

- Cerebras / Groq / OpenAI / OpenRouter / Gemini sono OpenAI-compatible → un
  unico client `openai.OpenAI(base_url=...)` (o l'SDK Cerebras nativo) funziona
  senza modifiche.
- Anthropic ha un'API diversa → un adapter sottile che espone la stessa forma
  (`.chat.completions.create` e `.models.list`).

Modulo dipendente solo da stdlib + offerte.config; gli SDK dei provider sono
importati in modo lazy in `build_client`, così l'assenza di un pacchetto degrada
con grazia (provider non disponibile) invece di rompere l'import.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from offerte.config import CEREBRAS_MODEL_BLACKLIST, DEFAULT_CEREBRAS_MODEL


@dataclass(frozen=True)
class Provider:
    label: str
    key_env: str
    kind: str  # "openai" | "anthropic"
    base_url: str | None
    default_models: tuple[str, ...]
    #: Ha un piano gratuito utilizzabile senza lasciare una carta. Il pannello
    #: delle chiavi mostra prima questi: è la differenza che conta per chi apre
    #: l'applicazione e non vuole spendere.
    free: bool = False
    #: Dove si ottiene la chiave. Senza il link, «incolla la tua chiave» è un
    #: vicolo cieco per chi non sa già dove andare.
    signup_url: str = ""


PROVIDERS: dict[str, Provider] = {
    "cerebras": Provider(
        "Cerebras",
        "CEREBRAS_API_KEY",
        "openai",
        "https://api.cerebras.ai/v1",
        ("zai-glm-4.7", "gpt-oss-120b"),
        free=True,
        signup_url="https://cloud.cerebras.ai",
    ),
    "groq": Provider(
        "Groq",
        "GROQ_API_KEY",
        "openai",
        "https://api.groq.com/openai/v1",
        ("llama-3.3-70b-versatile", "openai/gpt-oss-120b"),
        free=True,
        signup_url="https://console.groq.com/keys",
    ),
    "openai": Provider(
        "OpenAI",
        "OPENAI_API_KEY",
        "openai",
        None,
        ("gpt-4o-mini", "gpt-4o"),
        signup_url="https://platform.openai.com/api-keys",
    ),
    "openrouter": Provider(
        "OpenRouter",
        "OPENROUTER_API_KEY",
        "openai",
        "https://openrouter.ai/api/v1",
        ("anthropic/claude-3.5-sonnet", "google/gemini-flash-1.5"),
        free=True,
        signup_url="https://openrouter.ai/keys",
    ),
    "anthropic": Provider(
        "Anthropic",
        "ANTHROPIC_API_KEY",
        "anthropic",
        None,
        ("claude-sonnet-4-5", "claude-3-5-haiku-latest"),
        signup_url="https://console.anthropic.com/settings/keys",
    ),
    # Gemini passa dall'endpoint OpenAI-compatible di Google, non dall'SDK
    # `google-generativeai`. Due ragioni, entrambe concrete: quell'SDK trascina
    # `googleapiclient` e `grpc` — 111 MB nel bundle Windows, per un provider
    # che è uno di sei — e il suo adapter non sapeva elencare i modelli, quindi
    # su Gemini la scelta automatica leggeva una lista finta invece del catalogo
    # vero. Da qui `models.list()` funziona come per tutti gli altri.
    "gemini": Provider(
        "Google Gemini",
        "GEMINI_API_KEY",
        "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        ("gemini-2.0-flash", "gemini-2.5-pro"),
        free=True,
        signup_url="https://aistudio.google.com/apikey",
    ),
}

DEFAULT_PROVIDER = "cerebras"
_BLACKLIST = set(CEREBRAS_MODEL_BLACKLIST)


# --------------------------------------------------------------------------- #
# Selezione provider e chiavi
# --------------------------------------------------------------------------- #


def active_provider() -> str:
    """Provider attivo da env `AI_PROVIDER` (default cerebras; invalido → default)."""
    name = os.environ.get("AI_PROVIDER", "").strip().lower()
    return name if name in PROVIDERS else DEFAULT_PROVIDER


def get_api_key(provider: str) -> str:
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        return ""
    return os.environ.get(cfg.key_env, "").strip()


def is_configured(provider: str) -> bool:
    return bool(get_api_key(provider))


def configured_providers() -> list[str]:
    """Provider che hanno una API key impostata, nell'ordine del registry."""
    return [p for p in PROVIDERS if is_configured(p)]


def load_keys_from(secrets) -> None:
    """Copia in os.environ le chiavi provider + `AI_PROVIDER` presenti in `secrets`
    (es. `st.secrets`), senza sovrascrivere variabili già impostate. Permette al
    layer core (che legge os.environ) di vedere i secret della UI Streamlit."""
    names = [cfg.key_env for cfg in PROVIDERS.values()] + ["AI_PROVIDER"]
    for name in names:
        if os.environ.get(name):
            continue
        try:
            val = secrets.get(name, None)
        except Exception:
            val = None
        if val:
            os.environ[name] = str(val).strip()


# --------------------------------------------------------------------------- #
# Oggetti normalizzati in forma OpenAI
# --------------------------------------------------------------------------- #


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Completion:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _ModelInfo:
    def __init__(self, model_id: str, context_window: int = 0) -> None:
        self.id = model_id
        self.context_window = context_window


class _ModelList:
    def __init__(self, ids) -> None:
        self.data = [_ModelInfo(i) for i in ids]


class _Completions:
    def __init__(self, create) -> None:
        self.create = create


class _Chat:
    def __init__(self, create) -> None:
        self.completions = _Completions(create)


class _ModelsNS:
    def __init__(self, ids) -> None:
        self._ids = tuple(ids)

    def list(self):
        return _ModelList(self._ids)


# --------------------------------------------------------------------------- #
# Adapter Anthropic / Gemini → forma OpenAI
# --------------------------------------------------------------------------- #


class _AnthropicAdapter:
    """Espone `.chat.completions.create` e `.models.list` su un client Anthropic."""

    DEFAULT_MODELS = ("claude-sonnet-4-5", "claude-3-5-haiku-latest")

    def __init__(self, client) -> None:
        self._client = client
        self.chat = _Chat(self._create)
        self.models = _ModelsNS(self.DEFAULT_MODELS)

    def _create(self, *, model, messages, temperature: float = 0.1, max_tokens: int = 4096, **_):
        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        conv = [
            {
                "role": ("assistant" if m.get("role") == "assistant" else "user"),
                "content": m["content"],
            }
            for m in messages
            if m.get("role") != "system"
        ]
        resp = self._client.messages.create(
            model=model,
            system=system or None,
            messages=conv,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = "".join(getattr(b, "text", "") for b in (resp.content or []))
        return _Completion(text)


# --------------------------------------------------------------------------- #
# Costruzione client + selezione modello
# --------------------------------------------------------------------------- #


def _construct(factory, **kwargs):
    """Istanzia un client SDK con timeout e retry interno disattivato.

    `max_retries=0` è deliberato: il retry vive in `offerte.ai`
    (`cerebras_chat_with_retry`), che sa classificare gli errori e rinegoziare il
    modello su 404. Lasciando anche quello dell'SDK i due si moltiplicano — 4
    tentativi nostri × 2 dell'SDK = 8 chiamate per una singola richiesta, con
    l'attesa che ne consegue.

    Gli SDK che non accettano questi kwarg vengono costruiti senza: meglio un
    client senza timeout che nessun client.
    """
    from offerte.config import AI_REQUEST_TIMEOUT

    try:
        return factory(timeout=AI_REQUEST_TIMEOUT, max_retries=0, **kwargs)
    except TypeError:
        pass
    try:
        return factory(timeout=AI_REQUEST_TIMEOUT, **kwargs)
    except TypeError:
        return factory(**kwargs)


def build_client(provider: str | None = None):
    """Costruisce il client per `provider` (default = attivo). None se non
    configurato o SDK mancante."""
    provider = provider or active_provider()
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        return None
    key = get_api_key(provider)
    if not key:
        return None

    if cfg.kind == "openai":
        # Cerebras: preferisci l'SDK nativo (comportamento storico), poi openai-compat.
        if provider == "cerebras":
            try:
                from cerebras.cloud.sdk import Cerebras

                return _construct(Cerebras, api_key=key)
            except Exception:
                pass
        try:
            from openai import OpenAI
        except Exception:
            return None
        kwargs = {"api_key": key}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        return _construct(OpenAI, **kwargs)

    if cfg.kind == "anthropic":
        try:
            import anthropic
        except Exception:
            return None
        return _AnthropicAdapter(_construct(anthropic.Anthropic, api_key=key))

    return None


#: Quanti modelli sottoporre alla verifica di salute. Il catalogo di OpenRouter
#: ne ha centinaia: interrogarli tutti sarebbe centinaia di richieste per una
#: scelta sola. Si ordina prima per merito e si verifica solo la testa.
MAX_DA_VERIFICARE = 8


def ranked_models(provider: str | None = None, client=None, *, compito: str = "json") -> list[str]:
    """I modelli disponibili, dal migliore al peggiore.

    Tre passaggi, in quest'ordine perché ognuno costa più del precedente:
    catalogo del provider → ordinamento per nome e penalità accumulate → e solo
    per la testa della lista, se il provider è OpenRouter, la verifica live
    degli endpoint (gratuita, senza autenticazione, nessuna inferenza).
    """
    from offerte import model_selector

    provider = provider or active_provider()
    cfg = PROVIDERS.get(provider)
    candidates = cfg.default_models if cfg else (DEFAULT_CEREBRAS_MODEL,)
    if client is None:
        client = build_client(provider)
    if client is None:
        return list(candidates)

    try:
        data = list(getattr(client.models.list(), "data", []) or [])
        ids = [m.id for m in data if getattr(m, "id", None) and m.id not in _BLACKLIST]
    except Exception:
        return list(candidates)
    if not ids:
        return list(candidates)

    ordinati = model_selector.ordina(
        ids, provider=provider, preferiti=tuple(candidates), compito=compito
    )
    if provider != "openrouter":
        return ordinati

    testa = ordinati[:MAX_DA_VERIFICARE]
    try:
        from offerte import endpoint_health

        salute = endpoint_health.check_many(testa)
    except Exception:
        # Una verifica che non riesce non deve cambiare l'esito: i candidati
        # restano quelli, e sarà il failover a scartare chi non risponde.
        return ordinati
    riordinata = model_selector.ordina(
        testa, provider=provider, preferiti=tuple(candidates), compito=compito, salute=salute
    )
    return riordinata + ordinati[MAX_DA_VERIFICARE:]


def best_model(provider: str | None = None, client=None) -> str:
    """Il modello da usare adesso per questo provider."""
    ordinati = ranked_models(provider, client)
    if ordinati:
        return ordinati[0]
    cfg = PROVIDERS.get(provider or active_provider())
    return (cfg.default_models if cfg else (DEFAULT_CEREBRAS_MODEL,))[0]
