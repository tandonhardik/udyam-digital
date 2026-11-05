"""Smart KYC: OCR (Tesseract + OpenCV deskew) -> field extraction -> rule checks.

Images are handled as bytes in memory only; nothing is written to disk.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import cv2
import numpy as np
import pytesseract

from .config import ADDRESS_PROOF_MAX_AGE_DAYS, TODAY
from .data_store import get_store
from .gstin import normalise, validate_gstin, validate_udyam
from .privacy import get_logger, mask_id

log = get_logger("kyc")

# ---------------- OCR ----------------

def _deskew(gray: np.ndarray) -> np.ndarray:
    """Straighten tilted scans. Tries Tesseract OSD first, then min-area-rect on ink pixels."""
    try:
        osd = pytesseract.image_to_osd(gray, config="--psm 0", output_type=pytesseract.Output.DICT)
        rot = int(osd.get("rotate", 0))
        if rot:
            gray = np.rot90(gray, k=rot // 90 * -1 % 4)
    except Exception:
        pass
        
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    
    # CRITICAL FIX: Crop 5% of the edges before calculating the angle!
    # Otherwise, dark image borders will lock the minAreaRect angle to 0 degrees.
    h, w = ink.shape
    margin_y, margin_x = int(h * 0.05), int(w * 0.05)
    crop = ink[margin_y:h - margin_y, margin_x:w - margin_x]
    
    coords = np.column_stack(np.where(crop > 0))
    if len(coords) < 200:
        return gray
        
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle if angle < 45 else 90 - angle
    
    if abs(angle) < 0.5 or abs(angle) > 30:
        return np.ascontiguousarray(gray)
        
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _variants(data: bytes):
    """Yield OCR texts lazily, cheapest/most useful first. The 'clean' pass drops the pale watermark."""
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("Could not read image")
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    if max(gray.shape) < 1400:
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        
    clean = np.where(gray < 150, 0, 255).astype(np.uint8)
    
    # Pass 1 & 2: Standard attempts
    yield pytesseract.image_to_string(clean, config="--psm 6")
    yield pytesseract.image_to_string(gray, config="--psm 4")
    
    # Pass 3, 4, 5: Deskewed attempts utilizing PSM 4 (Assume a single column of text of variable sizes)
    deskewed_clean = _deskew(clean)
    deskewed_gray = _deskew(gray)
    
    yield pytesseract.image_to_string(deskewed_clean, config="--psm 4")
    yield pytesseract.image_to_string(deskewed_clean, config="--psm 6")
    yield pytesseract.image_to_string(deskewed_gray, config="--psm 4")
    
    # Pass 6: Last resort messy extraction
    yield pytesseract.image_to_string(clean, config="--psm 11")


def ocr_bytes(data: bytes) -> str:
    """Single best-effort text (used by tests / debugging)."""
    return next(iter(_variants(data)))

# ---------------- classification & label-based extraction ----------------

def classify(text: str) -> str:
    t = text.lower()
    if "udyam" in t:
        return "udyam"
    if "gst registration" in t or "gstin" in t or "goods and services tax" in t:
        return "gst"
    if any(k in t for k in ("electricity", "water bill", "utility", "units consumed", "bill date", "consumer no")):
        return "utility"
    if any(k in t for k in ("signatory", "identity", "id card", "valid till", "authorised")):
        return "id"
    return "unknown"


# label phrases per document type: field -> phrases (longest/most specific first)
LABELS = {
    "gst": {"gstin": ["gstin"], "legal": ["legal name"], "name": ["trade name"], "constitution": ["constitution"],
            "address": ["address of principal", "principal place", "address"], "reg_date": ["date of liability"]},
    "udyam": {"udyam": ["udyam reg. number", "udyam reg number", "udyam registration number", "udyam no"],
              "name": ["enterprise name", "name of enterprise"], "activity": ["major activity"], "city": ["city"],
              "state": ["state"], "reg_date": ["date of registration"]},
    "utility": {"consumer": ["consumer no"], "name": ["billed to", "consumer name"],
                "address": ["supply address", "service address", "address"], "bill_date": ["bill date", "date of bill", "issue date"],
                "units": ["units consumed"], "amount": ["amount due"]},
    "id": {"name": ["name"], "designation": ["designation"], "business": ["for business", "business"],
           "id_no": ["id no."], "valid_till": ["valid till", "valid upto", "valid until", "expiry"]},
}
NOISE = ("specimen", "synthetic", "accel ytl", "fictional", "format for hackathon")
ADDRESS_FIELDS = {"address"}


def _clean_line(l: str) -> str:
    return re.sub(r"\s+", " ", l.replace("|", " ")).strip()


def _starts_with_label(line: str, phrases: list[str]) -> str | None:
    low = line.lower().lstrip(" -:.")
    for ph in sorted(phrases, key=len, reverse=True):
        if re.match(rf"{re.escape(ph)}(?![a-z])", low):
            return ph
    return None


def read_fields(doc_type: str, text: str) -> dict:
    """Works on 'Label  value', 'Label: value', stacked 'Label / value' and wrapped (multi-line) values."""
    spec = LABELS.get(doc_type, {})
    all_phrases = [ph for v in spec.values() for ph in v] + ["place of business"]
    lines = [l for l in (_clean_line(x) for x in text.splitlines()) if l]
    lines = [l for l in lines if not any(n in l.lower() for n in NOISE)]
    out: dict = {}
    for field, phrases in spec.items():
        for i, line in enumerate(lines):
            ph = _starts_with_label(line, phrases)
            if not ph:
                continue
            low = line.lower().lstrip(" -:.")
            val = line[len(line) - len(low) + len(ph):].strip(" :-–")
            nxt = i + 1
            if not val and nxt < len(lines) and not _starts_with_label(lines[nxt], all_phrases):
                val, nxt = lines[nxt], nxt + 1                    # stacked layout
            if field in ADDRESS_FIELDS and val:
                while nxt < len(lines):
                    cont = lines[nxt]
                    pb = re.match(r"place of business\s*(.*)", cont, re.I)
                    if pb:
                        cont = pb.group(1)
                    elif _starts_with_label(cont, all_phrases) or re.match(r"[A-Za-z ]+\s\d{4}-\d{2}-\d{2}$", cont):
                        break
                    glue = "" if (len(val.split()[-1]) <= 2 and cont[:1].islower()) else " "   # mid-word wrap
                    val, nxt = (val + glue + cont).strip(), nxt + 1
                    if re.search(r"\d{6}\s*$", val):
                        break
            if val:
                out[field] = re.sub(r"\s*-{1,3}\s*(?=\d{6})", " - ", val)
            break
    return out


# ---- OCR-confusion repair for identifiers ----
_CONF = {"I": "T1L", "T": "I", "1": "IL", "L": "1I", "O": "0Q", "0": "O", "S": "5", "5": "S", "B": "8", "8": "B",
         "Z": "2", "2": "Z", "G": "6", "6": "G", "D": "0", "Q": "O"}


def repair_gstin(raw: str) -> tuple[str, bool]:
    """Return (gstin, corrected). Accepts a repair only if it passes the checksum and exists in the registry."""
    from itertools import combinations, product
    g = normalise(raw)
    g = re.sub(r"[^0-9A-Z]", "", g)[:16]
    if len(g) == 15 and validate_gstin(g)[0]:
        return g, False
    store = get_store()
    if len(g) == 16:                                   # OCR inserted a stray character: try deleting each one
        for i in range(16):
            cand = g[:i] + g[i + 1:]
            if validate_gstin(cand)[0] and store.lookup_gstin(cand):
                return cand, True
        return g, False
    if len(g) != 15:
        return g, False
    for n in (1, 2):
        for idxs in combinations(range(14), n):
            for subs in product(*[_CONF.get(g[i], "") for i in idxs]):
                c = list(g)
                for i, ch in zip(idxs, subs):
                    c[i] = ch
                cand = "".join(c)
                if validate_gstin(cand)[0] and store.lookup_gstin(cand):
                    return cand, True
    return g, False


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _dates(text: str) -> list[date]:
    out = []
    for m in re.finditer(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", text):
        try: out.append(date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError: pass
    for m in re.finditer(r"\b(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})\b", text):
        try: out.append(date(int(m[3]), int(m[2]), int(m[1])))
        except ValueError: pass
    for m in re.finditer(r"\b(\d{1,2})[-/. ]([A-Za-z]{3})[a-z]*[-/. ,]*(\d{4})\b", text):
        mo = _MONTHS.get(m[2].lower())
        if mo:
            try: out.append(date(int(m[3]), mo, int(m[1])))
            except ValueError: pass
    return out


def extract(doc_type: str, text: str) -> dict:
    """Extract ONLY the fields KYC needs (data minimisation)."""
    f = read_fields(doc_type, text)
    if doc_type == "gst":
        g = f.get("gstin", "")
        if not g:
            m = re.search(r"\b\d{2}[A-Z0-9]{13}\b", text.upper().replace(" ", ""))
            g = m.group(0) if m else ""
        f["gstin"] = normalise(g)
        f.pop("reg_date", None); f.pop("constitution", None)
    elif doc_type == "udyam":
        m = re.search(r"UDYAM[\s\-]*[A-Z]{2}[\s\-]*\d{2}[\s\-]*\d{7}", (f.get("udyam", "") + " " + text), re.I)
        f["udyam"] = re.sub(r"\s", "", m.group(0)).upper() if m else ""
        for k in ("activity", "city", "state", "reg_date"):
            f.pop(k, None)
    elif doc_type == "utility":
        ds = _dates(f.get("bill_date", "")) or _dates(text)
        f["bill_date"] = ds[0].isoformat() if ds else ""
        for k in ("consumer", "units", "amount"):
            f.pop(k, None)
    elif doc_type == "id":
        ds = _dates(f.get("valid_till", ""))
        f["valid_till"] = max(ds).isoformat() if ds else ""
        f.pop("id_no", None); f.pop("designation", None)
    return f


REQUIRED = {"gst": ["gstin", "address"], "udyam": ["name", "udyam"], "utility": ["bill_date", "address", "name"], "id": ["name", "valid_till"]}


def _complete(doc_type: str, fields: dict) -> bool:
    if not all(fields.get(k) for k in REQUIRED[doc_type]):
        return False
    if doc_type == "gst":                      # keep trying passes until the GSTIN is readable/repairable
        return validate_gstin(repair_gstin(fields["gstin"])[0])[0]
    return True


def read_document(data: bytes) -> tuple[str, dict, str]:
    """Run OCR passes until the document's required fields are found; merge fields across passes."""
    texts, doc_type, fields, gcands = [], "unknown", {}, []
    for txt in _variants(data):
        texts.append(txt)
        doc_type = classify(" ".join(texts))
        if doc_type == "unknown":
            continue
        got = extract(doc_type, txt)
        if got.get("gstin"):
            gcands.append(got["gstin"])
        for k, v in got.items():
            if v and not fields.get(k):
                fields[k] = v
        if doc_type == "gst" and gcands:       # prefer a candidate that validates after repair
            fields["gstin"] = next((c for c in gcands if validate_gstin(repair_gstin(c)[0])[0]), gcands[0])
        if _complete(doc_type, fields):
            break
    return doc_type, fields, " ".join(texts)


# ---------------- validation ----------------

_STOP = {"pvt", "ltd", "private", "limited", "llp", "and", "the", "m/s", "ms", "prop", "proprietor"}


def _tok(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower().replace("&", " and ")) if w not in _STOP}


def names_match(a: str, b: str, threshold: float = 0.6) -> bool:
    ta, tb = _tok(a), _tok(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= threshold


def addr_match(a: str, b: str, threshold: float = 0.85) -> bool:
    """Character-level comparison (robust to OCR line wraps that split words)."""
    from difflib import SequenceMatcher
    na, nb = (re.sub(r"[^a-z0-9]", "", (x or "").lower()) for x in (a, b))
    return bool(na and nb) and SequenceMatcher(None, na, nb).ratio() >= threshold


def _issue(code: str, severity: str, msg: str, doc: str = "") -> dict:
    return {"code": code, "severity": severity, "doc": doc, "message": msg}


def validate(docs: dict[str, dict]) -> dict:
    """docs = {"gst": {...fields}, "udyam": {...}, "utility": {...}, "id": {...}} -> verdict."""
    store = get_store()
    issues: list[dict] = []
    checks: list[dict] = []

    def ok(label):
        checks.append({"label": label, "passed": True})

    for need in ("gst", "udyam", "utility", "id"):
        if need not in docs:
            issues.append(_issue("MISSING_DOC", "error", f"Missing document: {need.upper()}", need))

    gst, udy, ut, idd = (docs.get(k, {}) for k in ("gst", "udyam", "utility", "id"))
    reg_gst = reg_udy = None

    # --- GSTIN ---
    if gst:
        g = gst.get("gstin", "")
        if not g:
            issues.append(_issue("GSTIN_MISSING", "error", "GSTIN could not be read from the GST certificate", "gst"))
        else:
            fixed, corrected = repair_gstin(g)
            if corrected:
                issues.append(_issue("GSTIN_OCR_CORRECTED", "info",
                                     f"Scan read the GSTIN as {mask_id(g)}; corrected to {mask_id(fixed)} (checksum and registry match)", "gst"))
                g = gst["gstin"] = fixed
            valid, why = validate_gstin(g)
            if not valid:
                issues.append(_issue("GSTIN_INVALID", "error", why, "gst"))
            else:
                ok("GSTIN format and checksum valid")
                reg_gst = store.lookup_gstin(g)
                if not reg_gst:
                    issues.append(_issue("GSTIN_NOT_FOUND", "error", "GSTIN not found in the GST registry", "gst"))
                else:
                    st = reg_gst["_status"]
                    if st.strip().lower() != "active":
                        issues.append(_issue("GSTIN_" + st.upper(), "error", f"GSTIN is {st} in the registry, so GST-dependent products cannot be sold", "gst"))
                    else:
                        ok("GSTIN is Active in registry")
                    if gst.get("name") and not (names_match(gst["name"], reg_gst["_name"]) or names_match(gst["name"], reg_gst["_trade"])):
                        issues.append(_issue("GST_NAME_MISMATCH", "error", "Name on certificate differs from the registry", "gst"))

    # --- Udyam ---
    if udy:
        u = udy.get("udyam", "")
        if not u:
            issues.append(_issue("UDYAM_MISSING", "error", "Udyam registration number not found on the certificate. Please re-upload a clear copy.", "udyam"))
        else:
            valid, why = validate_udyam(u)
            if not valid:
                issues.append(_issue("UDYAM_INVALID", "error", why, "udyam"))
            else:
                ok("Udyam number format valid")
                reg_udy = store.lookup_udyam(u)
                if not reg_udy:
                    issues.append(_issue("UDYAM_NOT_FOUND", "error", "Udyam number not found in the Udyam registry", "udyam"))
                else:
                    st = reg_udy["_status"]
                    if st.strip().lower() not in ("active", "registered", "valid"):
                        issues.append(_issue("UDYAM_" + st.upper(), "error", f"Udyam registration status is {st}", "udyam"))
                    else:
                        ok("Udyam found and active in registry")
                    if udy.get("name") and not names_match(udy["name"], reg_udy["_name"]):
                        issues.append(_issue("UDYAM_NAME_MISMATCH", "error", "Enterprise name on the Udyam certificate differs from the registry", "udyam"))

    # --- Utility bill age (frozen clock) ---
    if ut:
        bd = ut.get("bill_date")
        if not bd:
            issues.append(_issue("BILL_DATE_MISSING", "error", "Bill date could not be read from the address proof", "utility"))
        else:
            d = date.fromisoformat(bd)
            age = (TODAY - d).days
            if age < 0:
                issues.append(_issue("BILL_FUTURE", "error", f"Bill is dated in the future ({bd})", "utility"))
            elif age > ADDRESS_PROOF_MAX_AGE_DAYS:
                issues.append(_issue("BILL_STALE", "error", f"Address proof is {age} days old; it must be within {ADDRESS_PROOF_MAX_AGE_DAYS} days", "utility"))
            else:
                ok(f"Address proof is {age} days old (limit {ADDRESS_PROOF_MAX_AGE_DAYS})")

    # --- Signatory ID validity ---
    if idd and idd.get("valid_till"):
        vt = date.fromisoformat(idd["valid_till"])
        if vt < TODAY:
            issues.append(_issue("ID_EXPIRED", "error", f"Signatory ID expired on {vt.isoformat()}", "id"))
        else:
            ok("Signatory ID is in date")

    # --- Cross-document consistency ---
    names = {k: v.get("name") or v.get("business") for k, v in docs.items() if v.get("name") or v.get("business")}
    ref = (reg_gst or {}).get("_trade") or (reg_gst or {}).get("_name") or gst.get("name") or ""
    for k in ("udyam", "utility", "id"):
        n = (docs.get(k, {}).get("business") if k == "id" else docs.get(k, {}).get("name"))
        if n and ref and not names_match(n, ref) and not names_match(n, gst.get("name", "")):
            issues.append(_issue("NAME_MISMATCH", "error", f"Name on the {k.upper()} document does not match the GST registration", k))
    if not any(i["code"] == "NAME_MISMATCH" for i in issues) and len(names) > 1:
        ok("Business and signatory names are consistent")
    if gst.get("address") and ut.get("address"):
        if addr_match(gst["address"], ut["address"]):
            ok("Address matches between GST certificate and bill")
        else:
            issues.append(_issue("ADDRESS_MISMATCH", "error", "Address on the bill does not match the GST principal place of business", "utility"))
    if reg_gst and gst.get("address") and reg_gst["_address"] and not addr_match(gst["address"], reg_gst["_address"]):
        issues.append(_issue("GST_ADDRESS_MISMATCH", "warning", "Address on the GST certificate differs from the registry", "gst"))
    if reg_gst and idd.get("name") and "propriet" in (reg_gst.get("constitution") or "").lower():
        if not names_match(idd["name"], reg_gst["_name"]):
            issues.append(_issue("SIGNATORY_NOT_PROPRIETOR", "warning",
                                 "Signatory is not the registered proprietor; an authorisation letter would be needed", "id"))
        else:
            ok("Signatory matches the registered proprietor")

    errors = [i for i in issues if i["severity"] == "error"]
    verdict = "approved" if not errors else "rejected"
    log.info("KYC verdict=%s gstin=%s issues=%s", verdict, mask_id(gst.get("gstin")), [i["code"] for i in issues])
    return {"verdict": verdict, "checks": checks, "issues": issues,
            "gst_status": (reg_gst or {}).get("_status"), "reference_date": TODAY.isoformat()}


def process_upload(files: list[tuple[str, bytes]]) -> dict:
    """files = [(filename, bytes)]. Returns per-document extraction + overall verdict."""
    docs: dict[str, dict] = {}
    per_file = []
    for fname, data in files:
        try:
            dtype, fields, _ = read_document(data)
        except Exception as e:  # unreadable image
            per_file.append({"file": fname, "type": "unreadable", "error": str(e)})
            continue
        if dtype != "unknown":
            docs[dtype] = fields
        shown = {k: (mask_id(v) if k in ("gstin", "udyam") else v) for k, v in fields.items()}
        per_file.append({"file": fname, "type": dtype, "fields": shown})
    result = validate(docs)
    result["documents"] = per_file
    # raw bytes go out of scope here; nothing was persisted
    return result
