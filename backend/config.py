"""Central configuration. One place to change paths, the frozen clock and limits."""
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("UDYAM_DATA_DIR", ROOT / "data"))

# The case README says: use 2026-07-15 as "today" for every date check.
TODAY = date.fromisoformat(os.getenv("UDYAM_TODAY", "2026-07-15"))
ADDRESS_PROOF_MAX_AGE_DAYS = 90

# Optional: set GROQ_API_KEY to let an LLM do intent extraction / explanations.
# Without it the advisor runs fully offline on the rule-based fallback (demo-safe).
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# Groq retires models over time; check console.groq.com/docs/models and override via env if needed.
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# KYC images are processed in memory and never written to disk (DPDP storage limitation).
CONSENT_VERSION = "v1-2026-07"
CONSENT_RETENTION_TEXT = (
    "Uploaded document images are processed in memory and discarded immediately. "
    "Only the extracted fields needed for verification are kept, for the life of your "
    "order plus 90 days, then deleted."
)
