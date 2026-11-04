"""Conversational advisor.

LLM (optional) = understands what the owner said and explains the result.
Rules engine (rules.py) = decides what can be sold.  If no API key is set, or the
LLM call fails, a keyword extractor keeps the demo working offline.
"""
from __future__ import annotations

import re

from . import llm, rules
from .data_store import get_store
from .privacy import get_logger

log = get_logger("advisor")

TIER1 = {"delhi", "mumbai", "bengaluru", "bangalore", "chennai", "hyderabad", "kolkata", "pune", "ahmedabad", "new delhi"}

BUSINESS_SEGMENT = {
    "kirana": "micro", "shop": "micro", "salon": "micro", "pharmacy": "micro", "store": "micro",
    "clinic": "professional", "doctor": "professional", "ca ": "professional", "chartered": "professional",
    "coaching": "professional", "office": "professional", "lawyer": "professional",
    "restaurant": "multisite", "chain": "multisite", "outlets": "multisite", "branches": "multisite",
    "factory": "manufacturer", "manufactur": "manufacturer", "unit": "manufacturer", "textile": "manufacturer",
    # Hindi / Hinglish
    "किराना": "micro", "दुकान": "micro", "सैलून": "micro", "दवा": "micro", "फार्मेसी": "micro", "बेकरी": "micro", "मिठाई": "micro",
    "क्लिनिक": "professional", "डॉक्टर": "professional", "कोचिंग": "professional", "वकील": "professional", "ऑफिस": "professional", "सीए": "professional",
    "रेस्टोरेंट": "multisite", "शाखा": "multisite", "आउटलेट": "multisite", "चेन": "multisite",
    "फैक्ट्री": "manufacturer", "कारखाना": "manufacturer", "फैक्टरी": "manufacturer", "निर्माण": "manufacturer",
}
NEED_WORDS = {
    "internet": ["internet", "broadband", "wifi", "wi-fi", "fibre", "fiber", "leased line", "इंटरनेट", "ब्रॉडबैंड", "वाईफाई"],
    "sim": ["sim", "mobile", "postpaid", "phone line", "सिम", "मोबाइल"],
    "whatsapp": ["whatsapp", "व्हाट्सएप", "व्हाट्सऐप"],
    "sms": ["sms", "bulk message", "एसएमएस"],
    "telephony": ["telephony", "ivr", "toll-free", "toll free", "call centre", "landline", "sip"],
    "email": ["email", "e-mail", "office suite", "ईमेल"],
    "backup": ["backup", "back up", "cloud storage", "बैकअप"],
    "website": ["website", "web site", "domain", "वेबसाइट"],
    "pos": ["pos", "billing", "upi", "card machine", "payment", "inventory", "पेमेंट", "बिलिंग"],
    "security": ["antivirus", "security", "cyber", "virus", "ransomware", "firewall", "सिक्योरिटी"],
    "cctv": ["cctv", "camera", "surveillance", "कैमरा", "सीसीटीवी"],
    "wifi": ["guest wifi", "guest wi-fi", "wifi for customers", "customer wifi"],
    "failover": ["failover", "backup line", "backup internet", "internet goes down"],
    "sdwan": ["sd-wan", "sdwan", "connect my branches", "connect branches"],
    "staticip": ["static ip", "fixed ip"],
    "iot": ["iot", "machine monitoring", "sensor", "machine uptime"],
    "fleet": ["fleet", "vehicle tracking", "gps", "tracker", "delivery vehicles"],
}

T = {
    "en": {
        "hello": "Namaste! I'm your Accel Business advisor. Tell me about your business: what do you run, and where?",
        "ask_employees": "How many people work in your business (including you)?",
        "ask_sites": "How many shops/offices/sites do you have?",
        "ask_city": "Which city is the business in?",
        "ask_needs": "What do you need right now? For example internet, business mobile lines, CCTV, WhatsApp, email, billing/POS, security.",
        "ask_gstin": "Do you have a GSTIN? Some products can only be sold to GST-registered businesses. (yes / no)",
        "rec": "Based on what you told me, here is your bundle. About {monthly} per month (+GST) and {setup} one-time setup. You can toggle add-ons on the next screen.",
        "why": "Why: ",
        "skipped": "I left out {p} because: {r}.",
    },
    "hi": {
        "hello": "नमस्ते! मैं आपका Accel Business सलाहकार हूँ। अपने व्यापार के बारे में बताइए: आप क्या चलाते हैं और कहाँ?",
        "ask_employees": "आपके व्यापार में कुल कितने लोग काम करते हैं (आप सहित)?",
        "ask_sites": "आपकी कितनी दुकानें/ऑफिस हैं?",
        "ask_city": "आपका व्यापार किस शहर में है?",
        "ask_needs": "आपको अभी क्या चाहिए? जैसे इंटरनेट, बिज़नेस सिम, CCTV, WhatsApp मैसेज, ईमेल, सिक्योरिटी।",
        "ask_gstin": "क्या आपके पास GSTIN है? कुछ सेवाएँ सिर्फ़ GST-पंजीकृत व्यापार को मिलती हैं। (हाँ / नहीं)",
        "rec": "आपकी जानकारी के आधार पर यह आपका बंडल है: लगभग {monthly} प्रति माह (+GST) और {setup} एक बार का सेटअप। अगली स्क्रीन पर ऐड-ऑन चुन सकते हैं।",
        "why": "कारण: ",
        "skipped": "{p} शामिल नहीं किया क्योंकि: {r}।",
    },
}

HI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10}


SCRIPTS = [("hi", "Hindi", "\u0900-\u097F"), ("bn", "Bengali", "\u0980-\u09FF"), ("pa", "Punjabi", "\u0A00-\u0A7F"),
           ("gu", "Gujarati", "\u0A80-\u0AFF"), ("ta", "Tamil", "\u0B80-\u0BFF"), ("te", "Telugu", "\u0C00-\u0C7F"),
           ("kn", "Kannada", "\u0C80-\u0CFF"), ("ml", "Malayalam", "\u0D00-\u0D7F")]
LANG_NAMES = {"en": "English", **{c: n for c, n, _ in SCRIPTS}}


def detect_lang(text: str) -> str:
    """Script-based. Devanagari is treated as Hindi (Marathi readers are served Hindi unless Groq translates)."""
    for code, _, rng in SCRIPTS:
        if re.search(f"[{rng}]", text):
            return code
    return "en"


def _int_near(text: str, words: list[str]) -> int | None:
    t = text.translate(HI_DIGITS).lower()
    for w in words:
        m = re.search(rf"(\d+)\s*[-\s]*(?:{w})", t) or re.search(rf"(?:{w})\s*[:\-]?\s*(\d+)", t)
        if m:
            return int(m.group(1))
        for nw, n in NUM_WORDS.items():
            if re.search(rf"\b{nw}\b\s*[-\s]*(?:{w})", t):
                return n
    return None


def fallback_extract(text: str, state: dict) -> dict:
    t = text.translate(HI_DIGITS).lower()
    out: dict = {}
    emp = _int_near(t, ["employees", "people", "staff", "workers", "members", "person", "कर्मचारी", "लोग", "स्टाफ"])
    if emp is None and state.get("awaiting") == "employees":
        m = re.search(r"\d+", t)
        emp = int(m.group()) if m else None
    if emp is not None:
        out["employees"] = emp
    sites = _int_near(t, ["sites", "shops", "stores", "outlets", "branches", "offices", "locations", "दुकान", "ऑफिस", "शाखा"])
    if sites is None and state.get("awaiting") == "sites":
        m = re.search(r"\d+", t)
        sites = int(m.group()) if m else None
    if sites is not None:
        out["sites"] = sites
    m = re.search(r"tier\s*[-]?\s*([12])", t)
    if m:
        out["city_tier"] = int(m.group(1))
    elif state.get("awaiting") == "city":
        out["city_tier"] = 1 if any(c in t for c in TIER1) else 2
        out["city"] = text.strip()[:40]
    needs = [n for n, ws in NEED_WORDS.items() if any(w in t for w in ws)]
    if needs and state.get("awaiting") != "gstin":
        out["needs"] = needs
    known = get_store().business_types            # real business types from smb_leads.csv
    for bt in sorted(known, key=len, reverse=True):
        if bt in t or bt.rstrip("s") in t:
            out["segment"], out["business_type"] = known[bt], bt
            break
    else:
        for k, seg in BUSINESS_SEGMENT.items():
            if k in t:
                out["segment"] = seg
                out["business_type"] = k.strip()
                break
    if "segment" not in out and state.get("awaiting") == "type" and text.strip():
        # unknown business type: accept it as given rather than asking forever
        out["business_type"] = text.strip()[:40]
        out["segment"] = "multisite" if (out.get("sites") or state.get("sites") or 1) >= 3 else "micro"
    if state.get("awaiting") == "gstin":
        out["has_gstin"] = bool(re.search(r"\b(yes|y|haan|ha|hai)\b|हाँ|हां|है", t)) and not re.search(r"\b(no|nahi|nahin)\b|नहीं", t)
    return out


def llm_extract(text: str, state: dict) -> dict | None:
    if not llm.enabled():
        return None
    prompt = (
        "Extract facts about a small business from the owner's message (any Indian language). "
        "Return ONLY a JSON object with optional keys: business_type (str), segment (micro|professional|multisite|manufacturer), "
        "employees (int), sites (int), city (str), city_tier (1|2), has_gstin (bool), "
        f"needs (list, subset of {list(NEED_WORDS)}). Omit keys that are not mentioned. "
        f"The assistant just asked about: {state.get('awaiting')}. Message: {text}"
    )
    raw_data = llm.chat_json(prompt)
    
    if not isinstance(raw_data, dict):
        return None
        
    # FIX: Force all JSON keys to lowercase to prevent case-sensitivity bugs!
    data = {str(k).lower().strip(): v for k, v in raw_data.items()}
    
    # Never trust the model blindly: keep only known keys with sane types.
    clean: dict = {}
    
    for k in ("employees", "sites"):
        if isinstance(data.get(k), (int, float)) and 0 < data[k] < 100000:
            clean[k] = int(data[k])
            
    if str(data.get("city_tier")) in ("1", "2"):
        clean["city_tier"] = int(data["city_tier"])
        
    # Force lowercase on the values as well
    seg = str(data.get("segment", "")).strip().lower()
    if seg in ("micro", "professional", "multisite", "manufacturer"):
        clean["segment"] = seg
        
    for k in ("business_type", "city"):
        if data.get(k):
            clean[k] = str(data[k]).strip()[:60]
            
    # Handle string booleans ("true", "yes") just in case
    has_gstin = data.get("has_gstin")
    if isinstance(has_gstin, bool):
        clean["has_gstin"] = has_gstin
    elif isinstance(has_gstin, str):
        clean["has_gstin"] = has_gstin.strip().lower() in ("true", "yes", "y", "1")
        
    needs = [str(n).lower() for n in (data.get("needs") or []) if str(n).lower() in NEED_WORDS]
    if needs:
        clean["needs"] = needs
        
    return clean or None


def llm_explain(rec: dict, lang: str) -> str | None:
    if not llm.enabled():
        return None
    lines = [f"{l['name']} x{l['qty']} Rs{l['monthly']}/mo" for l in rec["quote"]["lines"]]
    prompt = (f"Explain in 2-3 simple sentences, in {LANG_NAMES.get(lang, 'English')}, why this bundle suits a small "
              f"business owner. No jargon, do not invent products or prices: {lines}")
    return llm.chat(prompt, max_tokens=250)


def next_question(s: dict) -> str | None:
    for key, awaiting in (("segment_or_type", "type"), ("employees", "employees"), ("sites", "sites"),
                          ("city_tier", "city"), ("needs", "needs"), ("has_gstin", "gstin")):
        if key == "segment_or_type":
            if not (s.get("business_type") or s.get("segment")):
                return "type"
        elif key not in s or s[key] in (None, []):
            return awaiting
    return None


def step(state: dict, text: str) -> dict:
    """One conversational turn. `state` is the session dict (mutated)."""
    lang = state.setdefault("lang", detect_lang(text))
    if detect_lang(text) != "en":
        lang = state["lang"] = detect_lang(text)   # switch when the owner writes in a non-Latin script
    tr = T.get(lang, T["en"])
    state.setdefault("transcript", []).append({"role": "user", "text": text})
    facts = llm_extract(text, state) or fallback_extract(text, state)
    for k, v in facts.items():
        if v not in (None, "", []):
            state[k] = v

    # --- ADD THESE TWO LINES ---
    if "city" in state and "city_tier" not in state:
        state["city_tier"] = 1 if any(c in state["city"].lower() for c in TIER1) else 2
    # ---------------------------

    if state.get("segment") in ("micro", "professional") and "sites" not in state:
        state["sites"] = 1  # single-site by default; no need to ask

    q = next_question(state)
    if q:
        state["awaiting"] = q
        msg = tr["hello"] if q == "type" else tr[f"ask_{q}"]
        reply = {"message": msg, "done": False}
    else:
        state["awaiting"] = None
        prof = rules.Profile(
            employees=int(state.get("employees", 1)), sites=int(state.get("sites", 1)),
            city_tier=int(state.get("city_tier", 2)),
            # Claimed GSTIN is provisional here; /api/order re-checks against the KYC-verified registry status.
            gstin_status="Active" if state.get("has_gstin") else None,
            segment=state.get("segment", ""), needs=state.get("needs", []))
        # Until KYC verifies the GSTIN, GST-gated products are shown as upgrades, not sold.
        rec = rules.recommend(prof)
        q_ = rec["quote"]
        msg = tr["rec"].format(monthly=f"₹{q_['monthly_total']:,.0f}", setup=f"₹{q_['setup_total']:,.0f}")
        why = llm_explain(rec, lang if lang in T else "en")
        if why:
            msg += "\n\n" + tr["why"] + why
        for ex in rec["excluded"]:
            msg += "\n" + tr["skipped"].format(p=ex["product"], r="; ".join(ex["reasons"]))
        reply = {"message": msg, "done": True, "recommendation": rec,
                 "profile": {"employees": prof.employees, "sites": prof.sites, "city_tier": prof.city_tier,
                             "has_gstin": bool(state.get("has_gstin"))}}
    if lang not in T and llm.enabled():       # regional language: Groq translates the (English) reply
        tx = llm.chat(f"Translate into {LANG_NAMES[lang]}. Keep numbers, ₹ amounts and product names unchanged. "
                      f"Output only the translation.\n\n{reply['message']}", max_tokens=500)
        reply["message"] = tx or reply["message"]
    state["transcript"].append({"role": "assistant", "text": reply["message"]})
    
    return reply
