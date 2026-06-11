FROM python:3.11-slim

WORKDIR /app

# Dipendenze Python (layer cache: copia prima solo i requirements)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codice applicazione
COPY . .

EXPOSE 8501

ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Le chiavi provider e APP_PASSWORD si passano via variabili d'ambiente
# (vedi docker-compose.yml) oppure montando .streamlit/secrets.toml.
CMD ["streamlit", "run", "app.py"]
