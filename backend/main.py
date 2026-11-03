"""FastAPI app: serves the JSON API and the static mobile-first frontend."""
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import advisor, kyc, rules, tracker
from .config import CONSENT_RETENTION_TEXT, CONSENT_VERSION, TODAY
from .data_store import get_store
from .gstin import validate_gstin, validate_udyam
from .privacy import get_logger, mask_id

log = get_logger("api")
app = FastAPI(title="Project Udyam Digital")
SESSIONS: dict[str, dict] = {}
CONSENTS: dict[str, dict] = {}
KYC_RESULTS: dict[str, dict] = {}


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
    employees: int = 1
    sites: int = 1
    city_tier: int = 2
    contact_name: str = ""


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
    sid = body.session_id or uuid.uuid4().hex
    state = SESSIONS.setdefault(sid, {})
    reply = advisor.step(state, body.message)
    return {"session_id": sid, **reply}


# ---------------- quote ----------------
@app.post("/api/quote")
def quote(body: QuoteIn):
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
    if not body.accepted or not {"kyc_verification", "service_provisioning"} <= set(body.purposes):
        raise HTTPException(400, "Consent for all required purposes is needed to continue with KYC")
    CONSENTS[body.session_id] = {"version": CONSENT_VERSION, "purposes": body.purposes, "at": date.today().isoformat()}
    log.info("Consent recorded for session %s", body.session_id[:6])
    return {"recorded": True, "version": CONSENT_VERSION}


@app.post("/api/consent/withdraw")
def withdraw(session_id: str):
    CONSENTS.pop(session_id, None)
    KYC_RESULTS.pop(session_id, None)
    return {"withdrawn": True, "deleted": ["extracted KYC fields", "consent record"]}


@app.post("/api/kyc/verify")
async def kyc_verify(session_id: str, files: list[UploadFile] = File(...)):
    if session_id not in CONSENTS:
        raise HTTPException(403, "Consent is required before documents can be processed")
    payload = [(f.filename or "upload", await f.read()) for f in files]
    result = kyc.process_upload(payload)
    KYC_RESULTS[session_id] = {"verdict": result["verdict"], "gst_status": result.get("gst_status")}
    return result


# ---------------- order + tracker ----------------
@app.post("/api/order")
def order(body: OrderIn):
    k = KYC_RESULTS.get(body.session_id)
    if not k or k["verdict"] != "approved":
        raise HTTPException(409, "KYC must be approved before placing an order")
    prof = rules.Profile(employees=body.employees, sites=body.sites, city_tier=body.city_tier, gstin_status=k.get("gst_status"))
    q = rules.build_quote(body.items, prof)
    if not q["valid"]:
        raise HTTPException(422, {"message": "Bundle breaks eligibility rules", "violations": q["violations"]})
    o = tracker.place_order(q["lines"], {k_: q[k_] for k_ in ("monthly_total", "monthly_with_gst", "setup_total", "contract_months")},
                            {"name": body.contact_name}, k["verdict"])
    return o


@app.get("/api/order/{order_id}")
def order_status(order_id: str, as_of: date | None = None):
    s = tracker.status(order_id, as_of)
    if not s:
        raise HTTPException(404, "Order not found")
    return s


# ---------------- frontend ----------------
FRONT = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONT), name="static")


@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")
