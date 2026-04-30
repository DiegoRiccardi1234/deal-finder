"""
knowledge_base.py — Gestione knowledge base prodotti per Trova Prezzi Mio.

- Si aggiorna automaticamente via Cerebras ogni 7 giorni (thread background).
- Se il JSON non esiste o è scaduto viene rigenerato al primo accesso al sito.
- Traccia item sconosciuti incontrati nella chat → li elabora al prossimo update.
- Genera kb_update_report.md dopo ogni aggiornamento.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_KB_PATH = _DATA_DIR / "knowledge_base.json"
_UNKNOWN_PATH = _DATA_DIR / "kb_unknown_items.json"
_REPORT_PATH = _DATA_DIR / "kb_update_report.md"
_UPDATE_INTERVAL_DAYS = 7

_lock = threading.Lock()
_update_in_progress = False

# ── Schema base (usato come fallback e seed per Cerebras) ─────────────────────
_BASE_KB: dict[str, Any] = {
    "updated_at": "2000-01-01T00:00:00",
    "version": 0,
    "categorie": {
        "smartphone": {
            "brands": ["Apple", "Samsung", "Xiaomi", "Google", "OnePlus", "Motorola", "Nothing"],
            "modelli": [
                "iPhone 15", "iPhone 15 Pro", "iPhone 15 Pro Max",
                "iPhone 16", "iPhone 16 Pro", "iPhone 16 Pro Max",
                "Samsung Galaxy S24", "Samsung Galaxy S24+", "Samsung Galaxy S24 Ultra",
                "Samsung Galaxy S25", "Samsung Galaxy S25+", "Samsung Galaxy S25 Ultra",
                "Xiaomi 14", "Xiaomi 14 Ultra", "Xiaomi 15",
                "Google Pixel 9", "Google Pixel 9 Pro", "Google Pixel 9 Pro XL",
                "OnePlus 13", "Nothing Phone (3)",
            ],
            "specs_importanti": ["storage", "RAM", "display_size", "fotocamera", "batteria", "OS"],
            "domande_guida": [
                "Preferisci iPhone (iOS) o Android?",
                "Quale modello ti interessa o che fascia cerchi?",
                "Quanto storage ti serve? (128 / 256 / 512 GB)",
                "Priorità: fotocamera, batteria, o prestazioni?",
                "Nuovo o ricondizionato/usato?",
            ],
            "fasce_prezzo": {"budget": "< 300€", "medio": "300–700€", "top": "> 700€"},
            "novita_2026": [],
        },
        "laptop": {
            "brands": ["Apple", "Lenovo", "ASUS", "HP", "Dell", "Acer", "MSI", "Razer", "Microsoft"],
            "modelli": [
                "MacBook Air 13 M3", "MacBook Air 15 M3", "MacBook Pro 14 M4", "MacBook Pro 16 M4",
                "Lenovo ThinkPad X1 Carbon Gen 12", "Lenovo IdeaPad 5 15", "Lenovo Legion 5i Gen 9",
                "ASUS ZenBook 14 OLED", "ASUS ROG Zephyrus G14", "ASUS TUF Gaming A15",
                "HP Spectre x360 14", "HP EliteBook 840 G11", "HP Pavilion 15",
                "Dell XPS 13 Plus", "Dell XPS 15", "Dell Latitude 7450",
                "Acer Aspire 5", "Acer Predator Helios 18", "MSI Stealth 16 Studio",
                "Microsoft Surface Laptop 6",
            ],
            "specs_importanti": ["RAM", "storage", "display_size", "GPU", "CPU", "peso", "autonomia"],
            "domande_guida": [
                "Per che uso? (lavoro/studio, gaming, grafica, portabilità)",
                "Windows, macOS, o Linux?",
                "Quanta RAM? (8 / 16 / 32 GB)",
                "Schermo: 13–14\" (leggero) o 15–16\" (potente)?",
                "GPU dedicata necessaria per gaming/grafica?",
            ],
            "fasce_prezzo": {"budget": "< 500€", "medio": "500–1200€", "top": "> 1200€"},
            "novita_2026": [],
        },
        "tablet": {
            "brands": ["Apple", "Samsung", "Lenovo", "Xiaomi", "Microsoft"],
            "modelli": [
                "iPad mini 7", "iPad 10a generazione", "iPad Air 13 M2",
                "iPad Pro 11 M4", "iPad Pro 13 M4",
                "Samsung Galaxy Tab S9 FE", "Samsung Galaxy Tab S9", "Samsung Galaxy Tab S9 Ultra",
                "Lenovo Tab P12 Pro", "Xiaomi Pad 6S Pro",
                "Microsoft Surface Pro 11",
            ],
            "specs_importanti": ["display_size", "storage", "connettività_LTE", "compatibilità_stilo"],
            "domande_guida": [
                "Uso principale: lettura, studio, disegno, gaming, o lavoro?",
                "Vuoi compatibilità con stilo (Pencil / S Pen)?",
                "Serve connettività LTE oltre al Wi-Fi?",
                "iPad o Android?",
            ],
            "fasce_prezzo": {"budget": "< 300€", "medio": "300–700€", "top": "> 700€"},
            "novita_2026": [],
        },
        "smartwatch": {
            "brands": ["Apple", "Samsung", "Garmin", "Fitbit", "Xiaomi", "Amazfit", "Polar", "Suunto"],
            "modelli": [
                "Apple Watch Series 10", "Apple Watch Ultra 2", "Apple Watch SE 2",
                "Samsung Galaxy Watch 7", "Samsung Galaxy Watch 7 Ultra",
                "Garmin Fenix 8", "Garmin Venu 3", "Garmin Forerunner 965",
                "Fitbit Charge 6", "Xiaomi Watch S3", "Amazfit GTR 4", "Polar Vantage V3",
            ],
            "specs_importanti": ["compatibilità_telefono", "autonomia_gg", "GPS", "sport_tracking", "ECG"],
            "domande_guida": [
                "Hai iPhone (→ Apple Watch) o Android?",
                "Uso principale: sport/running o notifiche/quotidiano?",
                "Ti serve autonomia lunga (> 5 gg)?",
                "GPS integrato necessario?",
            ],
            "fasce_prezzo": {"budget": "< 150€", "medio": "150–450€", "top": "> 450€"},
            "novita_2026": [],
        },
        "cuffie": {
            "brands": ["Sony", "Bose", "Apple", "Samsung", "Jabra", "Sennheiser", "JBL", "Bang & Olufsen"],
            "modelli": [
                "Sony WH-1000XM5", "Sony WH-1000XM6", "Sony WF-1000XM5",
                "Bose QuietComfort 45", "Bose QuietComfort Ultra Headphones",
                "Apple AirPods Pro 2", "Apple AirPods Max", "Apple AirPods 4",
                "Samsung Galaxy Buds3 Pro",
                "Jabra Evolve2 85", "Sennheiser Momentum 4 Wireless",
                "JBL Tune 770NC",
            ],
            "specs_importanti": ["tipo_cuffia", "ANC", "autonomia_ore", "wireless", "codec"],
            "domande_guida": [
                "Over-ear (padiglione) o in-ear (auricolari true wireless)?",
                "Noise cancelling attivo (ANC) necessario?",
                "Uso: musica, gaming, chiamate, sport, lavoro da casa?",
                "Hai iPhone (→ AirPods) o Android?",
            ],
            "fasce_prezzo": {"budget": "< 80€", "medio": "80–250€", "top": "> 250€"},
            "novita_2026": [],
        },
        "televisore": {
            "brands": ["Samsung", "LG", "Sony", "Philips", "Hisense", "TCL", "Panasonic"],
            "modelli": [
                "Samsung QE65S90D OLED", "Samsung QE65QN85D Neo QLED",
                "LG OLED65C4", "LG OLED55B4", "LG QNED87",
                "Sony XR-65X90L", "Sony XR-55A80L OLED",
                "Hisense 65U7NQ MiniLED", "TCL 65C805 MiniLED",
                "Philips OLED909",
            ],
            "specs_importanti": ["diagonale_pollici", "tecnologia_pannello", "risoluzione", "HDR", "HDMI_21", "smart_OS"],
            "domande_guida": [
                "Quanti pollici? (55\" / 65\" / 75\" / 85\")",
                "OLED (nero perfetto) o QLED/MiniLED (luminoso)?",
                "Lo usi per gaming? (HDMI 2.1 / VRR / 144Hz)",
                "Sistema operativo preferito: Tizen, webOS, Google TV?",
            ],
            "fasce_prezzo": {"budget": "< 500€", "medio": "500–1200€", "top": "> 1200€"},
            "novita_2026": [],
        },
        "console": {
            "brands": ["Sony", "Microsoft", "Nintendo"],
            "modelli": [
                "PlayStation 5", "PlayStation 5 Slim", "PlayStation 5 Pro",
                "Xbox Series X", "Xbox Series S",
                "Nintendo Switch 2", "Nintendo Switch OLED",
            ],
            "specs_importanti": ["esclusivi_giochi", "servizio_online", "retrocompatibilità", "4K"],
            "domande_guida": [
                "Hai già una console da aggiornare o parti da zero?",
                "PlayStation, Xbox o Nintendo?",
                "Gaming in TV (4K) o portatile (Switch)?",
                "Giocare online in multiplayer o single player?",
            ],
            "fasce_prezzo": {"budget": "< 300€", "medio": "300–600€", "top": "> 600€"},
            "novita_2026": [],
        },
        "fotocamera": {
            "brands": ["Sony", "Canon", "Nikon", "Fujifilm", "Panasonic", "GoPro", "DJI"],
            "modelli": [
                "Sony Alpha 7 IV", "Sony ZV-E10 II", "Sony FX30",
                "Canon EOS R50", "Canon EOS R8", "Canon EOS R6 Mark II",
                "Nikon Z50 II", "Nikon Z6 III",
                "Fujifilm X-T50", "Fujifilm X100VI",
                "GoPro Hero 13 Black", "DJI Osmo Action 5 Pro",
            ],
            "specs_importanti": ["tipo", "sensore", "video_4K", "stabilizzazione", "obiettivo_incluso"],
            "domande_guida": [
                "Mirrorless, compatta, o action cam?",
                "Livello: principiante, hobbista, semi-pro?",
                "Uso principale: ritratti, paesaggi, video, sport?",
                "Serve video 4K / slow motion?",
            ],
            "fasce_prezzo": {"budget": "< 500€", "medio": "500–1500€", "top": "> 1500€"},
            "novita_2026": [],
        },
        "abbigliamento": {
            "brands": ["Nike", "Adidas", "Zara", "H&M", "The North Face", "Levi's",
                       "Stone Island", "Ralph Lauren", "Tommy Hilfiger", "Carhartt"],
            "categorie_item": ["t-shirt", "felpa", "giacca", "cappotto", "pantaloni",
                               "jeans", "maglione", "camicia", "tuta sportiva", "shorts"],
            "specs_importanti": ["taglia", "genere", "stagione", "occasione", "materiale"],
            "domande_guida": [
                "Che capo cerchi? (t-shirt, giacca, pantaloni, felpa...)",
                "Taglia? (XS / S / M / L / XL / XXL — uomo o donna?)",
                "Uso: sportivo, casual, o elegante?",
                "Brand o stile preferito?",
            ],
            "fasce_prezzo": {"budget": "< 30€", "medio": "30–120€", "top": "> 120€"},
            "novita_2026": [],
        },
        "scarpe": {
            "brands": ["Nike", "Adidas", "New Balance", "Converse", "Vans",
                       "Timberland", "Hoka", "On Running", "Asics", "Saucony"],
            "categorie_item": ["sneakers", "scarpe da corsa", "trail running",
                               "stivali", "mocassini", "sandali", "scarpe eleganti"],
            "specs_importanti": ["numero_EU", "genere", "uso", "drop_mm"],
            "domande_guida": [
                "Che tipo? (sneakers casual, running, trail, formale, stivali)",
                "Numero EU?",
                "Uso: quotidiano, running, sport, elegante?",
                "Brand o modello specifico?",
            ],
            "fasce_prezzo": {"budget": "< 60€", "medio": "60–160€", "top": "> 160€"},
            "novita_2026": [],
        },
        "elettrodomestico": {
            "brands": ["Bosch", "Samsung", "LG", "Whirlpool", "Electrolux",
                       "Candy", "Beko", "Siemens", "Miele", "AEG"],
            "categorie_item": ["lavatrice", "lavasciuga", "frigorifero", "lavastoviglie",
                               "forno", "microonde", "aspirapolvere robot", "asciugatrice"],
            "specs_importanti": ["capacità_kg_lt", "classe_energetica", "dimensioni_cm", "rumorosità_dB"],
            "domande_guida": [
                "Che elettrodomestico cerchi?",
                "Dimensioni disponibili (larghezza / altezza)?",
                "Classe energetica minima (A / A+ / A++)?",
                "Brand preferito?",
            ],
            "fasce_prezzo": {"budget": "< 350€", "medio": "350–800€", "top": "> 800€"},
            "novita_2026": [],
        },
        "altro": {
            "brands": [],
            "modelli": [],
            "specs_importanti": [],
            "domande_guida": [
                "Che prodotto cerchi esattamente?",
                "Qual è il tuo budget?",
                "Nuovo o usato?",
            ],
            "fasce_prezzo": {},
            "novita_2026": [],
        },
    },
}


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_kb() -> dict[str, Any]:
    """Carica KB da disco. Ritorna _BASE_KB se mancante o corrotto."""
    try:
        if _KB_PATH.exists():
            with _lock:
                data = json.loads(_KB_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "categorie" in data:
                # Merge: assicura che categorie aggiunte dopo l'ultima scrittura esistano
                for cat, base_val in _BASE_KB["categorie"].items():
                    if cat not in data["categorie"]:
                        data["categorie"][cat] = base_val
                return data
    except Exception:
        pass
    return json.loads(json.dumps(_BASE_KB))  # deep copy


def needs_update(kb: dict[str, Any]) -> bool:
    """True se KB mai aggiornato da Cerebras o scaduto (> 7 giorni)."""
    try:
        ts = datetime.fromisoformat(str(kb.get("updated_at", "2000-01-01T00:00:00")))
        return datetime.now() - ts > timedelta(days=_UPDATE_INTERVAL_DAYS)
    except Exception:
        return True


def _save_kb(kb: dict[str, Any]) -> None:
    with _lock:
        _KB_PATH.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Cerebras update ───────────────────────────────────────────────────────────

def _get_model_name(client: Any) -> str:
    try:
        from offerte.ai import get_best_model
        return get_best_model(client) or "llama-3.3-70b"
    except Exception:
        return "llama-3.3-70b"


def _call_cerebras_for_category(client: Any, categoria: str, base_info: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Chiama Cerebras per aggiornare i dati di una categoria.
    Ritorna il JSON parsato o None se fallisce.
    """
    brands_str = ", ".join(base_info.get("brands", [])[:8])
    modelli_str = ", ".join((base_info.get("modelli") or base_info.get("categorie_item") or [])[:12])
    anno = datetime.now().year

    prompt = (
        f"Sei un esperto di e-commerce italiano aggiornato a {anno}.\n"
        f"Aggiorna i dati della categoria prodotti: **{categoria}**.\n"
        f"Dati attuali: brand=[{brands_str}], prodotti=[{modelli_str}]\n\n"
        f"Considera le novità uscite nel {anno} e nel {anno - 1}.\n"
        "Rispondi SOLO con JSON valido, senza testo aggiuntivo, in questo formato esatto:\n"
        "{\n"
        f'  "brands": ["max 10 brand principali per {categoria} venduti in Italia"],\n'
        f'  "modelli": ["max 20 modelli/prodotti più rilevanti e recenti per {categoria} in Italia nel {anno}. '
        f'Per abbigliamento/scarpe usa categorie generiche invece di modelli specifici."],\n'
        f'  "domande_guida": ["3-5 domande specifiche e utili per guidare l\'acquisto di {categoria}"],\n'
        f'  "fasce_prezzo": {{"budget": "< X€", "medio": "X-Y€", "top": "> Y€"}},\n'
        f'  "novita_{anno}": ["max 5 prodotti o trend nuovi rilevanti usciti nel {anno}. Lascia array vuoto se non ci sono novità certe."]\n'
        "}"
    )

    try:
        resp = client.chat.completions.create(
            model=_get_model_name(client),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=900,
        )
        raw = str(resp.choices[0].message.content if resp and resp.choices else "").strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            if isinstance(parsed, dict):
                return parsed
    except Exception as exc:
        print(f"[KB] Errore aggiornamento '{categoria}': {exc}")
    return None


def _generate_report(old_kb: dict[str, Any], new_kb: dict[str, Any], errors: list[str]) -> None:
    """Genera kb_update_report.md con le differenze rilevate."""
    anno = datetime.now().year
    lines = [
        "# Knowledge Base — Report Aggiornamento",
        f"**Data:** {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"**Versione KB:** {new_kb.get('version', '?')}",
        "",
        "## Modifiche per categoria",
        "",
    ]
    for cat, data in new_kb.get("categorie", {}).items():
        old_data = old_kb.get("categorie", {}).get(cat, {})
        old_models = set(old_data.get("modelli") or old_data.get("categorie_item") or [])
        new_models = set(data.get("modelli") or data.get("categorie_item") or [])
        added = new_models - old_models
        removed = old_models - new_models
        novita_key = f"novita_{anno}"
        novita = data.get(novita_key, [])

        changes = []
        if added:
            changes.append(f"**Aggiunti:** {', '.join(sorted(added))}")
        if removed:
            changes.append(f"**Rimossi:** {', '.join(sorted(removed))}")
        if novita:
            changes.append(f"**Novità {anno}:** {', '.join(novita)}")

        lines.append(f"### {cat.title()}")
        lines.append(f"- Brand: {', '.join(data.get('brands', []))}")
        if changes:
            for c in changes:
                lines.append(f"- {c}")
        else:
            lines.append("- Nessuna modifica rispetto alla versione precedente")
        lines.append("")

    if errors:
        lines += ["## Errori durante l'aggiornamento", ""]
        for err in errors:
            lines.append(f"- {err}")
        lines.append("")

    # Sezione item sconosciuti
    try:
        if _UNKNOWN_PATH.exists():
            unknowns: dict[str, list] = json.loads(_UNKNOWN_PATH.read_text(encoding="utf-8"))
            if any(unknowns.values()):
                lines += [
                    "## Item sconosciuti rilevati dagli utenti",
                    "*(questi item sono stati menzionati nelle chat ma non erano nel KB — valuta se integrarli)*",
                    "",
                ]
                for cat, items in unknowns.items():
                    if items:
                        lines.append(f"### {cat}")
                        for item in items:
                            lines.append(f"- {item}")
                lines.append("")
    except Exception:
        pass

    lines += [
        "---",
        f"*Report generato automaticamente da knowledge_base.py il {datetime.now().strftime('%d/%m/%Y %H:%M')}*",
    ]

    try:
        _REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
        print(f"[KB] Report salvato → {_REPORT_PATH.name}")
    except Exception as exc:
        print(f"[KB] Impossibile scrivere report: {exc}")


def _update_kb_worker(api_key: str) -> None:
    """Worker background: aggiorna ogni categoria via Cerebras e salva."""
    global _update_in_progress
    print("[KB] Inizio aggiornamento knowledge base via Cerebras...")

    try:
        from cerebras.cloud.sdk import Cerebras as _Cerebras
        client = _Cerebras(api_key=api_key)
    except Exception as exc:
        print(f"[KB] Impossibile inizializzare Cerebras: {exc}")
        _update_in_progress = False
        return

    old_kb = load_kb()
    new_kb = json.loads(json.dumps(old_kb))  # deep copy
    errors: list[str] = []
    anno = datetime.now().year

    for categoria, base_info in _BASE_KB["categorie"].items():
        if categoria == "altro":
            continue  # "altro" non ha senso da aggiornare
        print(f"[KB]   → {categoria}")
        result = _call_cerebras_for_category(client, categoria, base_info)
        if result:
            existing = dict(new_kb["categorie"].get(categoria, base_info))
            # Merge: Cerebras aggiorna brands, modelli, domande, fasce, novità
            for key in ("brands", "modelli", "categorie_item", "domande_guida", "fasce_prezzo"):
                val = result.get(key)
                if val:
                    existing[key] = val
            novita_key = f"novita_{anno}"
            if result.get(novita_key):
                existing[novita_key] = result[novita_key]
            new_kb["categorie"][categoria] = existing
        else:
            errors.append(f"{categoria}: aggiornamento fallito, mantenuti dati precedenti")
        time.sleep(0.8)  # rispetta rate limit Cerebras

    new_kb["updated_at"] = datetime.now().isoformat()
    new_kb["version"] = int(old_kb.get("version", 0)) + 1

    _save_kb(new_kb)
    _generate_report(old_kb, new_kb, errors)

    # Svuota unknown items dopo averli inclusi nel report
    try:
        _UNKNOWN_PATH.write_text("{}", encoding="utf-8")
    except Exception:
        pass

    print(f"[KB] Aggiornamento completato. Versione {new_kb['version']}. Errori: {len(errors)}")
    _update_in_progress = False


# ── API pubblica ──────────────────────────────────────────────────────────────

def init_kb_on_startup(api_key: str) -> None:
    """
    Chiama all'avvio dell'app (una volta per sessione Streamlit).
    Se KB assente o > 7 giorni vecchio, lancia aggiornamento in background.
    Non blocca la UI — l'aggiornamento avviene in parallelo.
    """
    global _update_in_progress
    if _update_in_progress or not api_key:
        return
    kb = load_kb()
    if needs_update(kb):
        _update_in_progress = True
        t = threading.Thread(target=_update_kb_worker, args=(api_key,), daemon=True, name="kb-updater")
        t.start()
        print("[KB] Thread aggiornamento avviato in background.")


def track_unknown(categoria: str, item: str) -> None:
    """
    Registra un item sconosciuto incontrato durante la chat.
    Viene incluso nel prossimo report di aggiornamento.
    """
    if not item or not categoria:
        return
    try:
        unknowns: dict[str, list] = {}
        if _UNKNOWN_PATH.exists():
            unknowns = json.loads(_UNKNOWN_PATH.read_text(encoding="utf-8"))
        bucket = unknowns.setdefault(categoria, [])
        if item not in bucket:
            bucket.append(item)
            with _lock:
                _UNKNOWN_PATH.write_text(json.dumps(unknowns, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_category_context(kb: dict[str, Any], categoria: str) -> str:
    """
    Ritorna una stringa di contesto KB per la categoria da iniettare nel system prompt.
    Mappa categorie generiche (tech, altro) alle sotto-categorie rilevanti.
    """
    _cat_map: dict[str, list[str]] = {
        "tech": ["smartphone", "laptop", "tablet", "cuffie", "smartwatch", "console", "fotocamera"],
        "smartphone": ["smartphone"],
        "laptop": ["laptop"],
        "tablet": ["tablet"],
        "smartwatch": ["smartwatch"],
        "cuffie": ["cuffie"],
        "console": ["console"],
        "fotocamera": ["fotocamera"],
        "televisore": ["televisore"],
        "abbigliamento": ["abbigliamento", "scarpe"],
        "scarpe": ["scarpe"],
        "sport": ["abbigliamento", "scarpe", "smartwatch"],
        "elettrodomestico": ["elettrodomestico"],
        "altro": [],
    }
    target = _cat_map.get(categoria.lower(), [categoria.lower()])
    categorie_kb = kb.get("categorie", {})
    anno = datetime.now().year
    lines: list[str] = []

    for cat in target:
        if cat not in categorie_kb:
            continue
        info = categorie_kb[cat]
        brands = info.get("brands", [])
        modelli = info.get("modelli") or info.get("categorie_item") or []
        domande = info.get("domande_guida", [])
        fasce = info.get("fasce_prezzo", {})
        novita = info.get(f"novita_{anno}", [])

        lines.append(f"[{cat.upper()}]")
        if brands:
            lines.append(f"  Brand: {', '.join(brands[:8])}")
        if modelli:
            lines.append(f"  Modelli noti: {', '.join(modelli[:16])}")
        if novita:
            lines.append(f"  Novità {anno}: {', '.join(novita[:5])}")
        if fasce:
            parts = [f"{k} {v}" for k, v in fasce.items() if v]
            lines.append(f"  Prezzi tipici: {' | '.join(parts)}")
        if domande:
            lines.append(f"  Domande guida: {' | '.join(domande[:3])}")

    if not lines:
        return ""

    updated = kb.get("updated_at", "")[:10]
    return f"KNOWLEDGE BASE PRODOTTI (aggiornato: {updated}):\n" + "\n".join(lines)


def get_status() -> str:
    """Stringa di stato leggibile per la sidebar."""
    kb = load_kb()
    ts = kb.get("updated_at", "mai")
    if ts == "2000-01-01T00:00:00":
        ts_label = "mai aggiornato"
    else:
        ts_label = ts[:16].replace("T", " ")
    ver = kb.get("version", 0)
    n_cat = len(kb.get("categorie", {}))
    suffix = " · aggiornamento in corso..." if _update_in_progress else ""
    return f"KB v{ver} · {n_cat} categorie · {ts_label}{suffix}"


def force_update(api_key: str) -> bool:
    """
    Forza un aggiornamento immediato (bloccante) — da usare solo in script/debug.
    In produzione usa init_kb_on_startup() che è non bloccante.
    """
    global _update_in_progress
    if _update_in_progress:
        return False
    _update_in_progress = True
    _update_kb_worker(api_key)
    return True
