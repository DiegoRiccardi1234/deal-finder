@echo off
REM Avvio rapido locale (Windows): crea la venv se manca, installa, lancia la UI.
setlocal
if not exist .venv (
  echo [setup] Creo l'ambiente virtuale...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
echo [setup] Installo le dipendenze...
pip install -r requirements.txt
echo [run] Avvio Streamlit su http://localhost:8501
streamlit run app.py
endlocal
