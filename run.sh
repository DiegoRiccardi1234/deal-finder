#!/usr/bin/env bash
# Avvio rapido locale (macOS/Linux): crea la venv se manca, installa, lancia la UI.
set -e
if [ ! -d .venv ]; then
  echo "[setup] Creo l'ambiente virtuale..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
echo "[setup] Installo le dipendenze..."
pip install -r requirements.txt
echo "[run] Avvio Streamlit su http://localhost:8501"
streamlit run app.py
