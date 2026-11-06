"""FastAPI app: JSON API + static mobile-first frontend + partner back-office + WhatsApp webhook."""
from __future__ import annotations

import hashlib
import hmac
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import advisor, config, kyc, rules, scoring, tracker
from .config import CONSENT_RETENTION_TEXT, CONSENT_VERSION, TODAY
from .data_store import get_store
from .gstin import validate_gstin, validate_udyam
from .privacy import get_logger, mask_id, mask_text

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.PARTNER_TOKEN_CONFIGURED:
        print(f"\n  Partner portal token (set PARTNER_TOKEN in .env to keep it fixed): {config.PARTNER_TOKEN}\n")
    yield


app = FastAPI(title="Project Udyam Digital", lifespan=lifespan)
SESSIONS: dict[str, dict] = {}
CONSENTS: dict[str, dict] = {}
KYC_RESULTS: dict[str, dict] = {}


# ---------------- helpers ----------------
def _now() -> float:
    return time.time()


def touch(state: dict, stage: str | None = None) -> None:
    """Record activity and advance (never regress) the funnel stage; the back-office reads both."""
    state["last_seen"] = _now()
    if stage and scoring.STAGES.index(stage) > scoring.STAGES.index(state.get("stage", "lead")):
        state["stage"] = stage


def get_state(session_id: str) -> dict:
    state = SESSIONS.get(session_id)
    if state is None:
        raise HTTPException(404, "Session not found")
    return state


def wa_session_id(phone: str) -> str:
    """Unguessable session id for a WhatsApp number (the phone number itself is never a session id or in a URL)."""
    return "wa-" + hmac.new(config.SESSION_SECRET.encode(), phone.encode(), hashlib.sha256).hexdigest()[:24]


def lead_ref(session_id: str) -> str:
    return hashlib.sha256((config.SESSION_SECRET + session_id).encode()).hexdigest()[:10]


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return ("*" * max(len(digits) - 4, 0) + digits[-4:]) if digits else ""


def profile_from_state(state: dict, gstin_status: str | None = None) -> rules.Profile:
    """The SERVER's record of the business is authoritative for eligibility, never numbers sent by the browser."""
    status = gstin_status if gstin_status is not None else ("Active" if state.get("has_gstin") else None)
    return rules.Profile(employees=int(state.get("employees", 1)), sites=int(state.get("sites", 1)),
                         city_tier=int(state.get("city_tier", 2)), gstin_status=status,
                         segment=state.get("segment", ""), needs=state.get("needs", []))


def require_partner(x_partner_token: str | None = Header(default=None)) -> None:
    if not x_partner_token or not hmac.compare_digest(x_partner_token, config.PARTNER_TOKEN):
        raise HTTPException(401, "Partner token required")


# ---------------- models ----------------
class ChatIn(BaseModel):
    session_id: str | None = None
    message: str


class QuoteIn(BaseModel):
    items: list[dict]
    employees: int = 1
    sites: int = 1
    city_tier: int = 2
    gstin_status: str | None = None


class ConsentIn(BaseModel):
    session_id: str
    purposes: list[str]
    accepted: bool


class OrderIn(BaseModel):
    session_id: str
    items: list[dict]
    employees: int = 1          # accepted for compatibility but ignored: eligibility uses the server-side session
    sites: int = 1
    city_tier: int = 2
    contact_name: str = ""


class ScoreIn(BaseModel):
    segment: str = "micro"
    city_tier: int = 2
    employees: int = 1
    sites: int = 1
    has_gstin: bool = False
    channel: str = "web"
    lang: str = "en"
    stage: str = "needs_assessed"


# ---------------- health / meta ----------------
@app.get("/api/health")
def health():
    s = get_store()
    return {"ok": True, "today": TODAY.isoformat(), "products": len(s.products),
            "gstin_records": len(s.gstin), "udyam_records": len(s.udyam)}


@app.get("/api/products")
def products():
    return [{"id": p.id, "name": p.name, "category": p.category, "monthly": p.price, "setup": p.setup_fee,
             "contract_months": p.contract_months, "install_days": p.install_days} for p in get_store().products.values()]


# ---------------- mock registries ----------------
@app.get("/api/lookup/gstin/{gstin}")
def lookup_gstin(gstin: str):
    valid, why = validate_gstin(gstin)
    if not valid:
        raise HTTPException(422, why)
    r = get_store().lookup_gstin(gstin)
    if not r:
        raise HTTPException(404, "GSTIN not found in registry")
    return {"gstin": mask_id(gstin), "legal_name": r["_name"], "trade_name": r["_trade"], "address": r["_address"], "status": r["_status"]}


@app.get("/api/lookup/udyam/{udyam}")
def lookup_udyam(udyam: str):
    valid, why = validate_udyam(udyam)
    if not valid:
        raise HTTPException(422, why)
    r = get_store().lookup_udyam(udyam)
    if not r:
        raise HTTPException(404, "Udyam number not found in registry")
    return {"udyam": mask_id(udyam), "name": r["_name"], "address": r["_address"], "status": r["_status"]}


# ---------------- advisor ----------------
@app.post("/api/chat")
def chat(body: ChatIn):
    # Only continue a session the server knows; client-chosen ids are never adopted.
    sid = body.session_id if body.session_id in SESSIONS else uuid.uuid4().hex
    state = SESSIONS.setdefault(sid, {"channel": "web"})
    reply = advisor.step(state, body.message)
    touch(state, "contacted")
    if reply.get("done"):
        touch(state, "needs_assessed")
    return {"session_id": sid, **reply}


@app.get("/api/session/{session_id}")
def get_session_state(session_id: str):
    """Hydrates the frontend when switching devices (WhatsApp -> web) or reloading."""
    state = get_state(session_id)
    prof = profile_from_state(state)
    rec = rules.recommend(prof)
    return {"profile": {"employees": prof.employees, "sites": prof.sites, "city_tier": prof.city_tier,
                        "has_gstin": bool(state.get("has_gstin"))},
            "recommendation": rec}


# ---------------- quote ----------------
@app.post("/api/quote")
def quote(body: QuoteIn):
    """Stateless price calculator for the UI. Placing an order re-validates everything server-side."""
    prof = rules.Profile(employees=body.employees, sites=body.sites, city_tier=body.city_tier, gstin_status=body.gstin_status)
    return rules.build_quote(body.items, prof)


# ---------------- consent + KYC ----------------
@app.get("/api/consent/notice")
def consent_notice():
    return {"version": CONSENT_VERSION, "controller": "Accel Business",
            "purposes": [
                {"id": "kyc_verification", "text": "Verify your business identity using your GST certificate, Udyam certificate, address proof and signatory ID.", "required": True},
                {"id": "service_provisioning", "text": "Use the verified details to set up and bill your services.", "required": True}],
            "data_collected": ["Business name", "GSTIN", "Udyam number", "Business address", "Utility bill date", "Signatory name and ID validity date"],
            "not_collected": ["ID numbers (Aadhaar/PAN)", "Photo of the signatory", "Bank details"],
            "retention": CONSENT_RETENTION_TEXT,
            "rights": "You can withdraw consent, access or correct your data, or ask for deletion at any time from the Privacy screen."}


@app.post("/api/consent")
def consent(body: ConsentIn):
    state = get_state(body.session_id)
    if not body.accepted or not {"kyc_verification", "service_provisioning"} <= set(body.purposes):
        raise HTTPException(400, "Consent for all required purposes is needed to continue with KYC")
    CONSENTS[body.session_id] = {"version": CONSENT_VERSION, "purposes": body.purposes, "at": datetime.now().isoformat(timespec="seconds")}
    touch(state, "quote_accepted")          # the owner accepted the quote and moved on to KYC
    log.info("Consent recorded for session %s", body.session_id[:6])
    return {"recorded": True, "version": CONSENT_VERSION}


@app.post("/api/consent/withdraw")
def withdraw(session_id: str):
    CONSENTS.pop(session_id, None)
    KYC_RESULTS.pop(session_id, None)
    state = SESSIONS.get(session_id)
    if state:
        state.pop("stuck_reason", None)
    return {"withdrawn": True, "deleted": ["extracted KYC fields", "consent record"]}


@app.post("/api/kyc/verify")
async def kyc_verify(session_id: str, files: list[UploadFile] = File(...)):
    state = get_state(session_id)
    if session_id not in CONSENTS:
        raise HTTPException(403, "Consent is required before documents can be processed")
    if len(files) > config.KYC_MAX_FILES:
        raise HTTPException(413, f"Please upload at most {config.KYC_MAX_FILES} documents")
    payload = []
    for f in files:
        data = await f.read(config.KYC_MAX_BYTES + 1)
        if len(data) > config.KYC_MAX_BYTES:
            raise HTTPException(413, f"{f.filename or 'file'} is larger than {config.KYC_MAX_BYTES // (1024 * 1024)} MB")
        payload.append((f.filename or "upload", data))
    result = kyc.process_upload(payload)
    KYC_RESULTS[session_id] = {"verdict": result["verdict"], "gst_status": result.get("gst_status")}
    if result["verdict"] == "approved":
        state.pop("stuck_reason", None)
        touch(state, "kyc_complete")
    else:
        state["stuck_reason"] = ", ".join(i["code"] for i in result["issues"] if i["severity"] == "error")
        touch(state)
    return result


# ---------------- order + tracker ----------------
@app.post("/api/order")
def order(body: OrderIn):
    state = get_state(body.session_id)
    if state.get("order_id") in tracker.ORDERS:                 # double-click / retry: return the same order
        return tracker.ORDERS[state["order_id"]]
    k = KYC_RESULTS.get(body.session_id)
    if not k or k["verdict"] != "approved":
        raise HTTPException(409, "KYC must be approved before placing an order")
    prof = profile_from_state(state, gstin_status=k.get("gst_status") or "")
    q = rules.build_quote(body.items, prof)
    if not q["valid"]:
        raise HTTPException(422, {"message": "Bundle breaks eligibility rules", "violations": q["violations"]})
    o = tracker.place_order(q["lines"], {k_: q[k_] for k_ in ("monthly_total", "monthly_with_gst", "setup_total", "contract_months")},
                            {"name": body.contact_name}, k["verdict"])
    state["order_id"] = o["order_id"]
    touch(state, "activated")
    return o


@app.get("/api/order/{order_id}")
def order_status(order_id: str, as_of: date | None = None):
    s = tracker.status(order_id, as_of)
    if not s:
        raise HTTPException(404, "Order not found")
    return s


# ---------------- Partner portal (back-office) : token protected ----------------
def _next_action(state: dict) -> str:
    stuck = state.get("stuck_reason") or ""
    if "GSTIN_" in stuck:
        return "Call: GST registration problem. Check status with the owner or offer the bundle without GST-only products."
    if "UDYAM_MISSING" in stuck or "BILL_" in stuck or "ID_" in stuck or "ADDRESS" in stuck or "NAME" in stuck:
        return "Assist: help the owner re-upload the correct document (visit or WhatsApp photo)."
    stage = state.get("stage", "lead")
    if stage == "quote_accepted":
        return "Assist: owner accepted the quote but did not finish KYC. Offer assisted document upload."
    if stage == "kyc_complete":
        return "Call now: KYC done, order not placed. Confirm install slot."
    if stage == "needs_assessed":
        return "Call: bundle was shown but not accepted. Confirm price and answer questions."
    return "Nurture on WhatsApp."


@app.get("/api/partner/model", dependencies=[Depends(require_partner)])
def partner_model():
    return scoring.model_info()


@app.get("/api/partner/leads", dependencies=[Depends(require_partner)])
def partner_leads():
    """Unfinished leads (not yet activated), best first. Identifiers are masked; no raw session ids are exposed."""
    now, rows = _now(), []
    for sid, st in SESSIONS.items():
        if not st.get("transcript") or st.get("stage") == "activated":
            continue
        idle = max(now - st.get("last_seen", now), 0) / 60
        stage = st.get("stage", "contacted")
        profile = {"segment": st.get("segment", ""), "city_tier": int(st.get("city_tier", 2)),
                   "employees": int(st.get("employees", 1)), "sites": int(st.get("sites", 1)),
                   "has_gstin": bool(st.get("has_gstin")), "channel": st.get("channel", "web"), "lang": st.get("lang", "en")}
        sc = scoring.score_lead(profile, stage)
        rows.append({"ref": lead_ref(sid), "business_type": st.get("business_type", "Unknown"),
                     "segment": scoring.SEGMENT_NAME.get(profile["segment"], "Unknown"), "city_tier": profile["city_tier"],
                     "employees": profile["employees"], "has_gstin": profile["has_gstin"],
                     "needs": ", ".join(st.get("needs", [])) or "Discovery pending",
                     "stage": scoring.STAGE_LABEL[stage], "status": "Dropped" if idle >= config.DROP_AFTER_MINUTES else "Active",
                     "idle_minutes": round(idle, 1), "stuck_reason": st.get("stuck_reason", ""), "channel": st.get("channel", "web"),
                     "phone": st.get("phone_masked", ""), "score": sc["score"], "priority": sc["priority"],
                     "reasons": sc["reasons"], "score_model": sc["model"], "next_action": _next_action(st),
                     "interactions": len(st["transcript"])})
    rows.sort(key=lambda r: (r["score"], -r["idle_minutes"]), reverse=True)
    return rows


@app.get("/api/partner/leads/{ref}/transcript", dependencies=[Depends(require_partner)])
def partner_transcript(ref: str):
    """Chat history so the rep does not re-ask the owner. Phone/GSTIN/Udyam patterns are masked."""
    for sid, st in SESSIONS.items():
        if lead_ref(sid) == ref:
            return {"ref": ref, "transcript": [{"role": m["role"], "text": mask_text(m["text"])} for m in st.get("transcript", [])]}
    raise HTTPException(404, "Lead not found")


@app.post("/api/leads/score", dependencies=[Depends(require_partner)])
def score_lead(body: ScoreIn):
    sc = scoring.score_lead(body.model_dump(), body.stage)
    return {"propensity_score": sc["score"], "priority": sc["priority"], "reasons": sc["reasons"], "model": sc["model"],
            "recommendation": "Dispatch to field sales / partner" if sc["priority"] == "High" else "Keep in digital nurture flow"}


# ---------------- WhatsApp (omnichannel) webhook ----------------
@app.post("/api/whatsapp")
def whatsapp_webhook(Body: str = Form(...), From: str = Form(...)):
    """Webhook for WhatsApp/SMS. (In production verify the provider's signature header before trusting the request.)"""
    sid = wa_session_id(From)
    state = SESSIONS.setdefault(sid, {"channel": "whatsapp", "phone_masked": mask_phone(From)})
    reply = advisor.step(state, Body)
    touch(state, "contacted")
    msg = reply["message"]
    if reply.get("done"):
        touch(state, "needs_assessed")
        msg += f"\n\nYour quote is ready. View details and upload your KYC documents:\n{config.PUBLIC_BASE_URL}/?page=quote&session={sid}"
    return Response(content=msg, media_type="text/plain; charset=utf-8")


# ---------------- frontend ----------------
FRONT = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONT), name="static")


@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")


@app.get("/partner")
def partner_index():
    return FileResponse(FRONT / "partner.html")


@app.get("/whatsapp")
def whatsapp_simulator():
    return FileResponse(FRONT / "whatsapp.html")