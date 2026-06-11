"""Astrazione multi-provider per i modelli AI.

Obiettivo: poter scegliere il backend LLM (Cerebras, Groq, OpenAI, OpenRouter,
Anthropic, Google Gemini) senza toccare i decine di call-site che usano la forma
OpenAI `client.chat.completions.create(...).choices[0].message.content`.

- Cerebras / Groq / OpenAI / OpenRouter sono OpenAI-compatible → un unico client
  `openai.OpenAI(base_url=...)` (o l'SDK Cerebras nativo) funziona senza modifiche.
- Anthropic e Gemini hanno API diverse → adapter sottili che espongono la stessa
  forma (`.chat.completions.create` e `.models.list`).

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
    kind: str                      # "openai" | "anthropic" | "gemini"
    base_url: str | None
    default_models: tuple[str, ...]


PROVIDERS: dict[str, Provider] = {
    "cerebras": Provider("Cerebras", "CEREBRAS_API_KEY", "openai",
                         "https://api.cerebras.ai/v1", ("zai-glm-4.7", "gpt-oss-120b")),
    "groq": Provider("Groq", "GROQ_API_KEY", "openai",
                     "https://api.groq.com/openai/v1", ("llama-3.3-70b-versatile", "openai/gpt-oss-120b")),
    "openai": Provider("OpenAI", "OPENAI_API_KEY", "openai",
                       None, ("gpt-4o-mini", "gpt-4o")),
    "openrouter": Provider("OpenRouter", "OPENROUTER_API_KEY", "openai",
                           "https://openrouter.ai/api/v1", ("anthropic/claude-3.5-sonnet", "google/gemini-flash-1.5")),
    "anthropic": Provider("Anthropic", "ANTHROPIC_API_KEY", "anthropic",
                          None, ("claude-sonnet-4-5", "claude-3-5-haiku-latest")),
    "gemini": Provider("Google Gemini", "GEMINI_API_KEY", "gemini",
                       None, ("gemini-2.0-flash", "gemini-2.5-pro")),
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
            {"role": ("assistant" if m.get("role") == "assistant" else "user"), "content": m["content"]}
            for m in messages if m.get("role") != "system"
        ]
        resp = self._client.messages.create(
            model=model, system=system or None, messages=conv,
            max_tokens=max_tokens, temperature=temperature,
        )
        text = "".join(getattr(b, "text", "") for b in (resp.content or []))
        return _Completion(text)


class _GeminiAdapter:
    """Espone la forma OpenAI su Google Generative AI."""
    DEFAULT_MODELS = ("gemini-2.0-flash", "gemini-2.5-pro")

    def __init__(self, model_factory) -> None:
        self._model_factory = model_factory
        self.chat = _Chat(self._create)
        self.models = _ModelsNS(self.DEFAULT_MODELS)

    def _create(self, *, model, messages, temperature: float = 0.1, **_):
        prompt = "\n\n".join(f"{m.get('role', 'user')}: {m['content']}" for m in messages)
        gm = self._model_factory(model)
        resp = gm.generate_content(prompt)
        return _Completion(getattr(resp, "text", "") or "")


# --------------------------------------------------------------------------- #
# Costruzione client + selezione modello
# --------------------------------------------------------------------------- #

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
                return Cerebras(api_key=key)
            except Exception:
                pass
        try:
            from openai import OpenAI
        except Exception:
            return None
        kwargs = {"api_key": key}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        return OpenAI(**kwargs)

    if cfg.kind == "anthropic":
        try:
            import anthropic
        except Exception:
            return None
        return _AnthropicAdapter(anthropic.Anthropic(api_key=key))

    if cfg.kind == "gemini":
        try:
            import google.generativeai as genai
        except Exception:
            return None
        genai.configure(api_key=key)
        return _GeminiAdapter(model_factory=lambda name: genai.GenerativeModel(name))

    return None


def best_model(provider: str | None = None, client=None) -> str:
    """Sceglie il miglior modello DISPONIBILE per il provider.

    Preferenza: primo candidato del registry effettivamente presente in
    `models.list()`; in mancanza, il modello col context_window più ampio
    (caso Cerebras); poi il primo disponibile; infine il primo candidato.
    """
    provider = provider or active_provider()
    cfg = PROVIDERS.get(provider)
    candidates = cfg.default_models if cfg else (DEFAULT_CEREBRAS_MODEL,)
    if client is None:
        client = build_client(provider)
    if client is None:
        return candidates[0]
    try:
        data = list(getattr(client.models.list(), "data", []) or [])
        ids = [m.id for m in data if getattr(m, "id", None) and m.id not in _BLACKLIST]
        for c in candidates:
            if c in ids:
                return c
        with_ctx = [m for m in data if getattr(m, "id", None) and getattr(m, "context_window", 0)]
        if with_ctx:
            with_ctx.sort(key=lambda m: m.context_window, reverse=True)
            return with_ctx[0].id
        if ids:
            return ids[0]
    except Exception:
        pass
    return candidates[0]
