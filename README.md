# 🚀 Project Udyam Digital

A telecom onboarding and KYC prototype for Accel Business SMBs: multilingual advisor -> dynamic quote -> Smart KYC -> live order tracker. It covers the core flow and all three stretch goals (WhatsApp channel, partner back-office, propensity scoring), and follows DPDP principles for data handling.

## Highlights

**Deterministic core, LLM only for language.** Groq (optional) is used only to understand what the owner wants and to translate. What may be sold and at what price is decided by a strict Python rules engine (catalogue dependencies, minimum employees and sites, GSTIN requirements). The model can never misquote. The engine is tested against all 5,000 real leads.

**Smart KYC.** Tesseract OCR with OpenCV straightening (tilted photos are levelled first, then orientation is checked), label-based field extraction, GSTIN checksum validation and registry lookup. If OCR misreads look-alike characters (I/T, 1/L, 5/S), the number is repaired only when the result passes the checksum, exists in the registry and is clearly the closest match. A genuinely wrong digit is never "fixed". Dates use the frozen clock (2026-07-15) and the 90-day address-proof rule.

**Privacy.** Consent before any document is read; images processed in memory only; masked logs; upload limits; orders are re-validated server-side from the session profile (browser-sent headcount is ignored) and are idempotent.

**Stretch goals**
* **WhatsApp:** `POST /api/whatsapp` (Twilio-style `Body` and `From`). The session id is an HMAC of the phone number, so the number never appears in a URL. The last reply links to the web quote page for the same session (session hydration).
* **Back-office:** lists leads that have not activated, with funnel stage, Active/Dropped status, why KYC failed, a score with reasons and a suggested next action. Transcripts are masked. Every partner endpoint needs the partner token.
* **Propensity scoring:** logistic regression on facts observable in a chat (segment, city tier, team size, sites, GSTIN, channel, language). Cross-validated AUC is about 0.71 and the scores are calibrated. The funnel stage supplies the starting probability (7% at lead creation, 63% after KYC) and the profile shifts it. Funnel flags are not model features because they would leak the label (about 0.96 AUC, which is misleading).

## Run locally

Prerequisites: Python 3.10+ and Tesseract OCR (Windows: https://github.com/UB-Mannheim/tesseract/wiki, add it to PATH).

```
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # Linux/macOS: cp .env.example .env
```
Edit `.env`: `GROQ_API_KEY` is optional (without it the offline advisor is used); set `PARTNER_TOKEN` to a value you choose.

Copy the four organiser CSVs into `data/` (`product_catalogue.csv`, `smb_leads.csv`, `mock_gstin_registry.csv`, `mock_udyam_registry.csv`), then:

```
python scripts/train_propensity.py
python -m uvicorn backend.main:app --reload --port 8000
```

| Page | URL |
|---|---|
| Customer app | http://localhost:8000 |
| WhatsApp simulator | http://localhost:8000/whatsapp |
| Partner back-office | http://localhost:8000/partner (click "Token" and enter `PARTNER_TOKEN`; if unset, a random token is printed in the server console) |

For a real phone, set `PUBLIC_BASE_URL` in `.env` to your public URL so the WhatsApp quote link works.

Sample documents in `kyc_documents/`: K01 intentionally has a Cancelled GSTIN and a missing Udyam number, so rejection is the expected result.

## Tests
```
python -m pytest -q                            # fast suite on small fixtures
python -m pytest -q tests/test_real_data.py    # engine against all 5,000 real leads
```

## Layout

```text
udyam-digital/
├── backend/
│   ├── main.py            API, sessions, partner auth
│   ├── advisor.py         multilingual discovery chat
│   ├── llm.py             Groq client (optional)
│   ├── rules.py           eligibility and pricing engine
│   ├── kyc.py             OCR and document checks
│   ├── scoring.py         lead propensity scoring
│   ├── tracker.py         order tracker
│   ├── privacy.py         masking for logs and transcripts
│   └── config.py          settings and limits
├── frontend/
│   ├── index.html         customer app
│   ├── app.js
│   ├── partner.html       back-office
│   ├── partner.js
│   └── whatsapp.html      WhatsApp simulator
├── scripts/
│   ├── train_propensity.py
│   └── check_data.py
└── tests/                 core, real-data, stretch goals, LLM client
```

## Known limits
* State is in memory (a restart clears sessions and orders); a real deployment would use a database and a message bus.
* The WhatsApp webhook must verify the provider's signature in production.
* Live Groq behaviour depends on the model you configure; the offline fallback keeps the app working if the call fails.