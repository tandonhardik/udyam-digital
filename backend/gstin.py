"""GSTIN / Udyam format validation.

GSTIN = 2-digit state code + 10-char PAN + entity digit + 'Z' + checksum char.
The dataset deliberately uses 'X' as the 4th PAN character, so we accept it
(real PAN entity types are P,C,H,F,A,T,B,L,J,G).  Checksum is the standard
GST mod-36 algorithm.
"""
import re

CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{3}[A-Z]{1}[A-Z]{1}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$")
UDYAM_RE = re.compile(r"^UDYAM-[A-Z]{2}-\d{2}-\d{7}$")


def gstin_checksum_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        v = CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return CHARS[(36 - total % 36) % 36]


def normalise(v: str | None) -> str:
    return re.sub(r"[\s]", "", (v or "")).upper()


def validate_gstin(gstin: str) -> tuple[bool, str]:
    g = normalise(gstin)
    if len(g) != 15:
        return False, "GSTIN must be exactly 15 characters"
    if not GSTIN_RE.match(g):
        return False, "GSTIN format is invalid"
    if not 1 <= int(g[:2]) <= 38:
        return False, "GSTIN state code is invalid"
    if gstin_checksum_char(g[:14]) != g[14]:
        return False, "GSTIN checksum does not match (likely a typo or tampered number)"
    return True, "ok"


def validate_udyam(udyam: str) -> tuple[bool, str]:
    u = normalise(udyam)
    if not UDYAM_RE.match(u):
        return False, "Udyam number must look like UDYAM-XX-00-0000000"
    return True, "ok"
