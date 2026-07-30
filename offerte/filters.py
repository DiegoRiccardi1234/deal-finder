"""offerte: offerte/filters.py"""

from __future__ import annotations

import re

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.parsing import *  # noqa: F401,F403


#: Parole che identificano un ACCESSORIO per un dispositivo, non il dispositivo.
#: Servono perché i risultati sono ordinati per prezzo crescente e un accessorio
#: costa una frazione del prodotto: cercando "iphone 15" la testa della classifica
#: era occupata da copriobiettivi e custodie da pochi euro, che contengono il nome
#: del modello e quindi passavano il filtro di rilevanza.
_ACCESSORY_WORDS: frozenset[str] = frozenset(
    {
        "custodia",
        "custodie",
        "cover",
        "case",
        "bumper",
        "pellicola",
        "pellicole",
        "vetro",
        "tempered",
        "screen protector",
        "salvaschermo",
        "cavo",
        "cavetto",
        "cable",
        "caricatore",
        "caricabatterie",
        "charger",
        "alimentatore",
        "adattatore",
        "adapter",
        "supporto",
        "stand",
        "cavalletto",
        "treppiede",
        "borsa",
        "custodia rigida",
        "sleeve",
        "zaino",
        "tracolla",
        "copriobiettivo",
        "cameralens",
        "camera lens",
        "camera glass",
        "lens protector",
        "protezione schermo",
        "protezione display",
        "protection kit",
        "screen glass",
        "glass",
        "powerbank",
        "power bank",
        "stilo",
        "stylus",
        "docking",
        "dock",
        "hub usb",
        "ricambio",
        "ricambi",
        "kit riparazione",
        "vetrino",
        "paraurti",
        # Pezzi di ricambio: frequenti sui marketplace dell'usato (Vinted,
        # Wallapop), dove costano una frazione del dispositivo e ne occupavano la
        # testa. NB: "display" e "batteria" sono deliberatamente ESCLUSI — sono
        # anche prodotti a sé (un monitor, una batteria esterna) e filtrarli
        # romperebbe quelle ricerche.
        "scheda madre",
        "motherboard",
        "logic board",
        "chassis",
        "telaio",
        "vetro posteriore",
        "back cover",
        "modulo fotocamera",
        "flat cable",
        # Vinted e Wallapop sono paneuropei e gli annunci arrivano nella lingua
        # del venditore: senza queste, cercando "iphone 15" i primi 12 risultati
        # a 1,00 € erano tutti custodie francesi, spagnole, tedesche e olandesi.
        "coque",
        "coques",
        "housse",
        "etui",
        "verre trempe",
        "funda",
        "fundas",
        "carcasa",
        "cargador",
        "protector de pantalla",
        "cristal templado",
        "hoesje",
        "hoesjes",
        "oplader",
        "screenprotector",
        "panzerglas",
        "ladegerat",
        "capa",
        "capas",
        "skin",
        "adesivo",
        "sticker",
    }
)

#: Radici cercate come SOTTOSTRINGA, non come parola intera. Servono per le
#: lingue che compongono i sostantivi: in tedesco la custodia è "Handyhülle",
#: "Schutzhülle" o "Hüllen", e un match su parola intera le mancherebbe tutte.
#: Tenute separate perché la ricerca per sottostringa è più aggressiva: qui
#: stanno solo radici che non compaiono dentro parole italiane o inglesi.
_ACCESSORY_STEMS: frozenset[str] = frozenset({"hülle", "huelle", "hullen", "schutzfolie"})

#: Marchi che producono ESCLUSIVAMENTE accessori di protezione: se compaiono nel
#: nome, il prodotto non è il dispositivo cercato. Tenuti separati dalle parole
#: perché il nome dell'accessorio non sempre dice cosa sia ("SBS TESINCAMGLIP15").
#: Volutamente esclusi i marchi che fanno anche prodotti veri (Trust, Hama).
_ACCESSORY_BRANDS: frozenset[str] = frozenset(
    {
        "cellularline",
        "cellular line",
        "otterbox",
        "spigen",
        "panzerglass",
        "nillkin",
        "ringke",
        "supcase",
        "sbs",
        "puro",
        "tucano",
        "uag",
        "urban armor gear",
    }
)


def looks_like_accessory(nome: str, query_tokens: list[str]) -> bool:
    """True se `nome` è un accessorio ma la query cercava il dispositivo.

    Se la query nomina già un accessorio ("custodia iphone 15") il filtro non
    scatta: in quel caso gli accessori sono il risultato voluto.

    LIMITE NOTO: è una lista curata, quindi per definizione incompleta — copre
    italiano, inglese, francese, spagnolo, tedesco, olandese e portoghese perché
    Vinted e Wallapop sono paneuropei, ma un annuncio in una lingua o con un
    termine non previsti passa. La soluzione strutturale sarebbe una soglia di
    plausibilità sul prezzo (un accessorio costa una frazione del dispositivo),
    che però rischia di scartare un affare vero: da valutare a parte.
    """
    query_text = " ".join(str(t).lower() for t in (query_tokens or []))
    if any(w in query_text for w in (*_ACCESSORY_WORDS, *_ACCESSORY_STEMS)):
        return False
    nome_lower = str(nome or "").lower()
    if any(stem in nome_lower for stem in _ACCESSORY_STEMS):
        return True
    for word in (*_ACCESSORY_WORDS, *_ACCESSORY_BRANDS):
        if " " in word:
            if word in nome_lower:
                return True
        elif re.search(rf"\b{re.escape(word)}\b", nome_lower):
            return True
    return False


def _passes_hard_spec_filters(offerta: Offerta, filtri: dict[str, str]) -> bool:
    """Applica vincoli tecnici hard per ridurre falsi positivi su notebook/smartphone."""
    return len(_hard_spec_mismatch_reasons(offerta, filtri)) == 0


def _hard_spec_mismatch_reasons(offerta: Offerta, filtri: dict[str, str]) -> list[str]:
    """Restituisce i motivi di mismatch hard (RAM/storage/display), lista vuota se passa."""
    if not filtri:
        return []

    reasons: list[str] = []

    search_text = f"{offerta.nome} " + " ".join(
        str(v) for v in (offerta.specs or {}).values() if v not in (None, "", [], {})
    )
    search_lower = search_text.lower()

    ram_target = filtri.get("ram_gb") or filtri.get("ram")
    if ram_target:
        m = re.search(r"(\d{1,3})", str(ram_target))
        if m:
            target = int(m.group(1))
            gb_vals = _extract_ram_gb_values(search_lower)
            if not gb_vals or max(gb_vals) < target:
                found = f"trovato={max(gb_vals)}GB" if gb_vals else "trovato=assente"
                reasons.append(f"ram<{target}GB ({found})")

    storage_target = filtri.get("storage_gb") or filtri.get("storage")
    if storage_target:
        m = re.search(r"(\d{2,4})", str(storage_target))
        if m:
            target = int(m.group(1))
            gb_vals = _extract_storage_gb_values(search_lower)
            if not gb_vals:
                gb_vals = _extract_gb_values(search_lower)
            if not gb_vals or max(gb_vals) < target:
                found = f"trovato={max(gb_vals)}GB" if gb_vals else "trovato=assente"
                reasons.append(f"storage<{target}GB ({found})")

    size_target = filtri.get("size_inches") or filtri.get("display")
    if size_target:
        parsed_range = _parse_target_range(str(size_target))
        if parsed_range is not None:
            low, high = parsed_range
            inches_vals = _extract_inches_values(search_lower)
            if not inches_vals or not any(low <= v <= high for v in inches_vals):
                found = ",".join(f'{v:.1f}"' for v in inches_vals) if inches_vals else "assente"
                reasons.append(f'display fuori range {low:.1f}-{high:.1f}" (trovato={found})')

    return reasons


def is_relevant(nome: str, query_tokens: list[str], strict_specs: bool = True) -> bool:
    """
    Filtro di rilevanza: i token della query devono essere presenti nel nome
    del prodotto (o in uno dei loro alias normalizzati).

    Con strict_specs=False, i token di specifica tecnica (es. '16gb', 'ram')
    vengono saltati — le specs verranno verificate tramite AI enrichment.

    Per query corte (<=2 token), basta che almeno 1 token sia presente (OR logic)
    per supportare query generiche tipo 'scarpe', 'libro', ecc.

    Se TUTTI i token sono di specifica, saltarli non lascerebbe nulla da
    valutare: in quel caso si valutano comunque, invece di saltarli. Senza questa
    regola i due rami si comportavano in modo opposto e sbagliato — una query di
    due soli token-spec ("ssd 1tb") cadeva sul `return False` finale e scartava il
    100% dei prodotti da ogni fonte, mentre con tre token-spec il ramo AND
    accettava qualunque cosa per verità vacua, perfino un frullatore.
    """
    nome_lower = nome.lower()
    # Un accessorio che nomina il modello passerebbe qualunque controllo sui token:
    # va escluso prima, altrimenti domina l'ordinamento per prezzo crescente.
    if looks_like_accessory(nome, query_tokens):
        return False
    brand_tokens = [token for token in query_tokens if token in _TECH_BRANDS]
    # Se non resta nessun token da valutare, i token-spec tornano significativi:
    # "ssd 1tb" deve pur cercare "ssd" e "1tb" nel nome.
    skip_specs = not strict_specs and not all(_is_spec_token(t) for t in query_tokens)
    # Per query corte senza brand tech specifico, applica logica OR:
    # basta un token per considerare rilevante (supporta query generiche come "scarpe", "libro")
    if len(query_tokens) <= 2 and not brand_tokens:
        for token in query_tokens:
            if skip_specs and _is_spec_token(token):
                continue
            varianti = _ALIASES.get(token, {token})
            varianti.add(token)
            if any(v in nome_lower for v in varianti):
                return True
        return False
    for token in query_tokens:
        if skip_specs and _is_spec_token(token):
            continue
        if token.isdigit() and len(token) <= 2 and brand_tokens:
            if not any(
                re.search(rf"\b{re.escape(brand)}\s*{re.escape(token)}\b", nome_lower)
                for brand in brand_tokens
            ):
                return False
            continue

        # Espande il token con gli alias conosciuti
        varianti = _ALIASES.get(token, {token})
        varianti.add(token)
        if not any(v in nome_lower for v in varianti):
            return False
    return True
