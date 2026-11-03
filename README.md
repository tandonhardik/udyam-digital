# Project Udyam Digital — core prototype (no stretch goals)

Advisor -> Quote -> Consent + Smart KYC -> Order -> Tracker, on a phone-sized web UI.

## Run it
```bash
# 1. Python 3.10+ and the Tesseract OCR engine
#    Ubuntu/WSL: sudo apt install tesseract-ocr tesseract-ocr-hin
#    macOS:      brew install tesseract tesseract-lang
#    Windows:    install from https://github.com/UB-Mannheim/tesseract/wiki and add it to PATH
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Copy the organiser files in (all four CSVs)
#    data/product_catalogue.csv  data/smb_leads.csv  data/mock_gstin_registry.csv  data/mock_udyam_registry.csv
#    kyc_documents/  (for you to upload from during the demo)
python scripts/check_data.py        # every line must show a column, none "NOT FOUND"

# 3. Optional Groq LLM (free key at console.groq.com; otherwise the offline rule-based advisor is used)
export GROQ_API_KEY=gsk_...         # Windows: set GROQ_API_KEY=gsk_...  (optional: GROQ_MODEL=...)

# 4. Start
uvicorn backend.main:app --reload --port 8000
# open http://localhost:8000 on your laptop, or http://<laptop-ip>:8000 on a phone (add --host 0.0.0.0)
pytest tests/test_core.py -q        # unit tests (tiny fixtures, no real data needed)
pytest tests/test_real_data.py -q -s   # runs the engine on all 5,000 real leads (needs data/; run it separately)
```

## Layout
```
backend/  config.py  data_store.py  rules.py  gstin.py  kyc.py  advisor.py  llm.py  tracker.py  privacy.py  main.py
frontend/ index.html  style.css  app.js
scripts/  check_data.py
tests/    test_core.py  fixtures/ (fake CSVs)
```

## Data facts the code relies on (verified on the organiser pack)
- Price shown = midpoint of the catalogue range (activated customers pay ~0.48 of the way up the range, always inside it).
- Setup fee is per unit (price x qty); contract length = longest `min_contract_months` in the bundle.
- Default quantities follow the catalogue `unit`: per site/counter = number of sites, per user = employees, per line = employees/2 (max 6), cameras = 2/site.
- 48 of 368 activated leads were sold bundles that break the catalogue's own rules; `rules.validate_bundle` makes that impossible.
- K01's GSTIN is Cancelled in the registry; the registry has many look-alike business names, so KYC always looks up by GSTIN/Udyam number, never by name.
