# Test Suite

## Installazione

```bash
pip install -r requirements-test.txt
playwright install chromium
```

## Esecuzione

```bash
pytest tests/ -v
```

## Note

- Le UI test avviano Streamlit in modalita controllata con `APP_TEST_MODE=1`.
- In questa modalita le risposte AI e i risultati di ricerca sono deterministici, cosi la suite non dipende da rete o credenziali reali.