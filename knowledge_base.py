"""
knowledge_base.py — Gestione knowledge base prodotti per Deal Finder.

- Fornisce domande guida generali, senza generare fatti di prodotto con AI.
- Prezzi, disponibilità e specifiche correnti provengono dalle offerte trovate.
- Conserva la compatibilità con lo stato locale senza rigenerarlo all'avvio.

Lo stato vive in SQLite (`offerte.db`, tabelle `kb_state` e `kb_unknown_items`).
`data/knowledge_base.json` resta come seed iniziale di sola lettura: prima veniva
riscritto dall'updater, ed essendo tracciato da git sporcava il working tree a
ogni avvio della UI.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any

from offerte import paths
from offerte.db import get_db

# Seed di SOLA LETTURA, tracciato da git. Lo stato runtime della KB vive in
# SQLite (tabella `kb_state`): questo file non viene mai riscritto. Sta quindi
# fra i file *spediti* (nel bundle: dentro `_MEIPASS`), non fra quelli
# dell'utente — così un aggiornamento può portare un seed nuovo, che invece
# resterebbe congelato se stesse in `data/`, protetta dall'aggiornatore.
_KB_PATH = paths.bundle_dir() / "data" / "knowledge_base.json"
_UPDATE_INTERVAL_DAYS = 7

_lock = threading.Lock()

# Lock separato per il solo flag di "aggiornamento in corso". Deve essere
# distinto da `_lock`: quest'ultimo è già preso da `load_kb()`, che viene
# chiamata mentre si decide se avviare l'updater, quindi riusarlo qui
# significherebbe deadlock (Lock non è reentrant).
_update_flag_lock = threading.Lock()
_update_in_progress = False


def _try_claim_update() -> bool:
    """Test-and-set atomico del flag. True se il chiamante ha preso il turno.

    Prima era `if _update_in_progress: return` seguito da `_update_in_progress =
    True`: due rerun ravvicinati di Streamlit passavano entrambi il controllo e
    avviavano due updater sulla stessa knowledge base.
    """
    global _update_in_progress
    with _update_flag_lock:
        if _update_in_progress:
            return False
        _update_in_progress = True
        return True


def _release_update() -> None:
    global _update_in_progress
    with _update_flag_lock:
        _update_in_progress = False


# ── Schema locale storico, conservato per compatibilità ─────────────────────
_BASE_KB: dict[str, Any] = {
    "updated_at": "2000-01-01T00:00:00",
    "version": 0,
    "categorie": {
        "smartphone": {
            "brands": ["Apple", "Samsung", "Xiaomi", "Google", "OnePlus", "Motorola", "Nothing"],
            "modelli": [
                "iPhone 15",
                "iPhone 15 Pro",
                "iPhone 15 Pro Max",
                "iPhone 16",
                "iPhone 16 Pro",
                "iPhone 16 Pro Max",
                "Samsung Galaxy S24",
                "Samsung Galaxy S24+",
                "Samsung Galaxy S24 Ultra",
                "Samsung Galaxy S25",
                "Samsung Galaxy S25+",
                "Samsung Galaxy S25 Ultra",
                "Xiaomi 14",
                "Xiaomi 14 Ultra",
                "Xiaomi 15",
                "Google Pixel 9",
                "Google Pixel 9 Pro",
                "Google Pixel 9 Pro XL",
                "OnePlus 13",
                "Nothing Phone (3)",
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
            "brands": [
                "Apple",
                "Lenovo",
                "ASUS",
                "HP",
                "Dell",
                "Acer",
                "MSI",
                "Razer",
                "Microsoft",
            ],
            "modelli": [
                "MacBook Air 13 M3",
                "MacBook Air 15 M3",
                "MacBook Pro 14 M4",
                "MacBook Pro 16 M4",
                "Lenovo ThinkPad X1 Carbon Gen 12",
                "Lenovo IdeaPad 5 15",
                "Lenovo Legion 5i Gen 9",
                "ASUS ZenBook 14 OLED",
                "ASUS ROG Zephyrus G14",
                "ASUS TUF Gaming A15",
                "HP Spectre x360 14",
                "HP EliteBook 840 G11",
                "HP Pavilion 15",
                "Dell XPS 13 Plus",
                "Dell XPS 15",
                "Dell Latitude 7450",
                "Acer Aspire 5",
                "Acer Predator Helios 18",
                "MSI Stealth 16 Studio",
                "Microsoft Surface Laptop 6",
            ],
            "specs_importanti": [
                "RAM",
                "storage",
                "display_size",
                "GPU",
                "CPU",
                "peso",
                "autonomia",
            ],
            "domande_guida": [
                "Per che uso? (lavoro/studio, gaming, grafica, portabilità)",
                "Windows, macOS, o Linux?",
                "Quanta RAM? (8 / 16 / 32 GB)",
                'Schermo: 13–14" (leggero) o 15–16" (potente)?',
                "GPU dedicata necessaria per gaming/grafica?",
            ],
            "fasce_prezzo": {"budget": "< 500€", "medio": "500–1200€", "top": "> 1200€"},
            "novita_2026": [],
        },
        "tablet": {
            "brands": ["Apple", "Samsung", "Lenovo", "Xiaomi", "Microsoft"],
            "modelli": [
                "iPad mini 7",
                "iPad 10a generazione",
                "iPad Air 13 M2",
                "iPad Pro 11 M4",
                "iPad Pro 13 M4",
                "Samsung Galaxy Tab S9 FE",
                "Samsung Galaxy Tab S9",
                "Samsung Galaxy Tab S9 Ultra",
                "Lenovo Tab P12 Pro",
                "Xiaomi Pad 6S Pro",
                "Microsoft Surface Pro 11",
            ],
            "specs_importanti": [
                "display_size",
                "storage",
                "connettività_LTE",
                "compatibilità_stilo",
            ],
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
            "brands": [
                "Apple",
                "Samsung",
                "Garmin",
                "Fitbit",
                "Xiaomi",
                "Amazfit",
                "Polar",
                "Suunto",
            ],
            "modelli": [
                "Apple Watch Series 10",
                "Apple Watch Ultra 2",
                "Apple Watch SE 2",
                "Samsung Galaxy Watch 7",
                "Samsung Galaxy Watch 7 Ultra",
                "Garmin Fenix 8",
                "Garmin Venu 3",
                "Garmin Forerunner 965",
                "Fitbit Charge 6",
                "Xiaomi Watch S3",
                "Amazfit GTR 4",
                "Polar Vantage V3",
            ],
            "specs_importanti": [
                "compatibilità_telefono",
                "autonomia_gg",
                "GPS",
                "sport_tracking",
                "ECG",
            ],
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
            "brands": [
                "Sony",
                "Bose",
                "Apple",
                "Samsung",
                "Jabra",
                "Sennheiser",
                "JBL",
                "Bang & Olufsen",
            ],
            "modelli": [
                "Sony WH-1000XM5",
                "Sony WH-1000XM6",
                "Sony WF-1000XM5",
                "Bose QuietComfort 45",
                "Bose QuietComfort Ultra Headphones",
                "Apple AirPods Pro 2",
                "Apple AirPods Max",
                "Apple AirPods 4",
                "Samsung Galaxy Buds3 Pro",
                "Jabra Evolve2 85",
                "Sennheiser Momentum 4 Wireless",
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
                "Samsung QE65S90D OLED",
                "Samsung QE65QN85D Neo QLED",
                "LG OLED65C4",
                "LG OLED55B4",
                "LG QNED87",
                "Sony XR-65X90L",
                "Sony XR-55A80L OLED",
                "Hisense 65U7NQ MiniLED",
                "TCL 65C805 MiniLED",
                "Philips OLED909",
            ],
            "specs_importanti": [
                "diagonale_pollici",
                "tecnologia_pannello",
                "risoluzione",
                "HDR",
                "HDMI_21",
                "smart_OS",
            ],
            "domande_guida": [
                'Quanti pollici? (55" / 65" / 75" / 85")',
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
                "PlayStation 5",
                "PlayStation 5 Slim",
                "PlayStation 5 Pro",
                "Xbox Series X",
                "Xbox Series S",
                "Nintendo Switch 2",
                "Nintendo Switch OLED",
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
                "Sony Alpha 7 IV",
                "Sony ZV-E10 II",
                "Sony FX30",
                "Canon EOS R50",
                "Canon EOS R8",
                "Canon EOS R6 Mark II",
                "Nikon Z50 II",
                "Nikon Z6 III",
                "Fujifilm X-T50",
                "Fujifilm X100VI",
                "GoPro Hero 13 Black",
                "DJI Osmo Action 5 Pro",
            ],
            "specs_importanti": [
                "tipo",
                "sensore",
                "video_4K",
                "stabilizzazione",
                "obiettivo_incluso",
            ],
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
            "brands": [
                "Nike",
                "Adidas",
                "Zara",
                "H&M",
                "The North Face",
                "Levi's",
                "Stone Island",
                "Ralph Lauren",
                "Tommy Hilfiger",
                "Carhartt",
            ],
            "categorie_item": [
                "t-shirt",
                "felpa",
                "giacca",
                "cappotto",
                "pantaloni",
                "jeans",
                "maglione",
                "camicia",
                "tuta sportiva",
                "shorts",
            ],
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
            "brands": [
                "Nike",
                "Adidas",
                "New Balance",
                "Converse",
                "Vans",
                "Timberland",
                "Hoka",
                "On Running",
                "Asics",
                "Saucony",
            ],
            "categorie_item": [
                "sneakers",
                "scarpe da corsa",
                "trail running",
                "stivali",
                "mocassini",
                "sandali",
                "scarpe eleganti",
            ],
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
            "brands": [
                "Bosch",
                "Samsung",
                "LG",
                "Whirlpool",
                "Electrolux",
                "Candy",
                "Beko",
                "Siemens",
                "Miele",
                "AEG",
            ],
            "categorie_item": [
                "lavatrice",
                "lavasciuga",
                "frigorifero",
                "lavastoviglie",
                "forno",
                "microonde",
                "aspirapolvere robot",
                "asciugatrice",
            ],
            "specs_importanti": [
                "capacità_kg_lt",
                "classe_energetica",
                "dimensioni_cm",
                "rumorosità_dB",
            ],
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


def _read_seed_kb() -> dict[str, Any] | None:
    """Legge il seed `data/knowledge_base.json`, che è tracciato e di SOLA lettura."""
    try:
        if _KB_PATH.exists():
            data = json.loads(_KB_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "categorie" in data:
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def load_kb() -> dict[str, Any]:
    """Carica la KB: prima lo stato runtime in SQLite, poi il seed, poi _BASE_KB.

    L'intera sequenza gira sotto `_lock` perché il merge con `_BASE_KB` avveniva
    fuori dal lock che proteggeva la lettura: l'updater in background poteva
    scrivere nel frattempo e il merge partiva da dati già superati.
    """
    data: dict[str, Any] | None = None
    with _lock:
        try:
            row = get_db().query_one(
                "SELECT updated_at, version, categorie FROM kb_state WHERE id = 1"
            )
            if row is not None:
                data = {
                    "updated_at": row["updated_at"],
                    "version": int(row["version"]),
                    "categorie": json.loads(row["categorie"]),
                }
        except (sqlite3.Error, json.JSONDecodeError):
            data = None

        if data is None:
            data = _read_seed_kb()

        if isinstance(data, dict) and isinstance(data.get("categorie"), dict):
            # Merge: assicura che categorie aggiunte dopo l'ultima scrittura esistano
            for cat, base_val in _BASE_KB["categorie"].items():
                if cat not in data["categorie"]:
                    data["categorie"][cat] = base_val
            return data

    return json.loads(json.dumps(_BASE_KB))  # deep copy


def needs_update(kb: dict[str, Any]) -> bool:
    """Età dello stato storico; non avvia aggiornamenti automatici."""
    try:
        ts = datetime.fromisoformat(str(kb.get("updated_at", "2000-01-01T00:00:00")))
        return datetime.now() - ts > timedelta(days=_UPDATE_INTERVAL_DAYS)
    except Exception:
        return True


def _save_kb(kb: dict[str, Any]) -> None:
    """Persiste la KB in SQLite.

    NON scrive `data/knowledge_base.json`: quel file è tracciato da git e serve
    solo come seed iniziale. Prima veniva riscritto qui, quindi ogni avvio della
    UI lasciava il working tree sporco.
    """
    with _lock:
        try:
            get_db().execute(
                "INSERT INTO kb_state(id, updated_at, version, categorie) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, "
                "version = excluded.version, categorie = excluded.categorie",
                (
                    str(kb.get("updated_at", "")),
                    int(kb.get("version", 1) or 1),
                    json.dumps(kb.get("categorie", {}), ensure_ascii=False),
                ),
            )
        except (sqlite3.Error, TypeError, ValueError):
            pass


# ── API pubblica compatibile ─────────────────────────────────────────────────


def init_kb_on_startup(api_key: str) -> None:
    """Compatibilità: la guida locale non genera fatti di prodotto."""
    return None


def track_unknown(categoria: str, item: str) -> None:
    """
    Registra un item sconosciuto incontrato durante la chat.
    Viene incluso nel prossimo report di aggiornamento.
    """
    if not item or not categoria:
        return
    # Un solo INSERT: la coppia (categoria, item) è PRIMARY KEY, quindi il
    # "già presente?" lo decide il database. Prima era una lettura fuori dal lock
    # seguita da una scrittura dentro — due item concorrenti sulla stessa
    # categoria si sovrascrivevano a vicenda.
    try:
        get_db().execute(
            "INSERT OR IGNORE INTO kb_unknown_items(categoria, item, first_seen) VALUES (?, ?, ?)",
            (str(categoria), str(item), datetime.now().isoformat(timespec="seconds")),
        )
    except sqlite3.Error:
        pass


def get_category_context(kb: dict[str, Any], categoria: str) -> str:
    """Solo domande generali: i fatti provengono dalle offerte."""
    generic = {
        "tech": "uso previsto, budget, dimensioni, RAM e spazio necessari",
        "smartphone": "uso previsto, budget, dimensioni e spazio necessari",
        "laptop": "uso previsto, budget, dimensioni, RAM e spazio necessari",
        "abbigliamento": "taglia, materiale, colore e budget",
        "scarpe": "taglia, utilizzo, materiale e budget",
        "televisore": "dimensioni, distanza di visione, uso e budget",
        "elettrodomestico": "dimensioni disponibili, utilizzo e budget",
    }
    guide = generic.get(categoria.lower(), "uso previsto, requisiti essenziali e budget")
    return (
        f"GUIDA GENERALE ALLE DOMANDE: {guide}. "
        "Non costituisce un catalogo aggiornato. Per prezzi, disponibilita e "
        "specifiche usa solo i dati delle offerte; se mancano, dichiaralo."
    )


def get_status() -> str:
    """Stringa di stato leggibile per la sidebar."""
    return "Guida generale locale · specifiche e prezzi dalle offerte"


def force_update(api_key: str) -> bool:
    """Compatibilità: aggiornamento generativo della KB disattivato."""
    return False
