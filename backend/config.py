"""Central configuration. One place to change paths, the frozen clock, secrets and limits."""
import os
import secrets
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=ROOT / ".env")
DATA_DIR = Path(os.getenv("UDYAM_DATA_DIR", ROOT / "data"))

# The case README says: use 2026-07-15 as "today" for every date check.
TODAY = date.fromisoformat(os.getenv("UDYAM_TODAY", "2026-07-15"))
ADDRESS_PROOF_MAX_AGE_DAYS = 90

# ---- Optional LLM (Groq). Without a key the advisor runs fully offline on the rule-based fallback. ----
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or ""
# Groq retires models over time; check console.groq.com/docs/models and override via env if needed.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
# Tried automatically if the primary model errors or returns an empty answer.
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")

# ---- Back-office (partner portal) access. Secure by default: if no token is configured, a random one is
# generated at start-up and printed once in the server console. ----
PARTNER_TOKEN_CONFIGURED = bool(os.getenv("PARTNER_TOKEN"))
PARTNER_TOKEN = os.getenv("PARTNER_TOKEN") or secrets.token_urlsafe(9)
# Secret used to derive unguessable session ids for WhatsApp numbers (so a phone number is never a session id).
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(16)
# Base URL placed in WhatsApp "view your quote" links. Set to your public/ngrok URL when demoing on a phone.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
# A session idle for this long without activating is shown to the back-office as "Dropped".
DROP_AFTER_MINUTES = int(os.getenv("DROP_AFTER_MINUTES", "15"))

# ---- Upload limits for KYC documents ----
KYC_MAX_FILES = 8
KYC_MAX_BYTES = 8 * 1024 * 1024

# KYC images are processed in memory and never written to disk (DPDP storage limitation).
CONSENT_VERSION = "v1-2026-07"
CONSENT_RETENTION_TEXT = (
    "Uploaded document images are processed in memory and discarded immediately. "
    "Only the extracted fields needed for verification are kept, for the life of your "
    "order plus 90 days, then deleted."
)