"""Stretch goals: WhatsApp channel, back-office lead view (auth, masking, stages), propensity scoring, order integrity."""
import pytest
from fastapi.testclient import TestClient

from backend import config, scoring
from backend.main import CONSENTS, KYC_RESULTS, SESSIONS, app, wa_session_id

client = TestClient(app)
H = {"X-Partner-Token": config.PARTNER_TOKEN}


def wa(body, phone="+919812345678"):
    return client.post("/api/whatsapp", data={"Body": body, "From": phone})


def test_partner_endpoints_require_token():
    for path in ("/api/partner/leads", "/api/partner/model", "/api/partner/leads/x/transcript"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-Partner-Token": "wrong"}).status_code == 401
    assert client.post("/api/leads/score", json={}).status_code == 401
    assert client.get("/api/partner/model", headers=H).status_code == 200


def test_whatsapp_session_is_hashed_and_lead_visible_masked():
    phone = "+919812345678"
    r = wa("I run a kirana shop with 4 employees")
    assert r.status_code == 200 and "text/plain" in r.headers["content-type"]
    sid = wa_session_id(phone)
    assert sid in SESSIONS and phone not in sid and SESSIONS[sid]["channel"] == "whatsapp"
    leads = client.get("/api/partner/leads", headers=H).json()
    mine = [l for l in leads if l["phone"].endswith("5678")]
    assert mine and "919812" not in mine[0]["phone"] and "session_id" not in mine[0]
    assert mine[0]["stage"] in ("Contacted", "Needs assessed") and mine[0]["status"] in ("Active", "Dropped")
    t = client.get(f"/api/partner/leads/{mine[0]['ref']}/transcript", headers=H).json()["transcript"]
    assert t[0]["role"] == "user"


def test_transcript_masks_identifiers():
    wa("my number is 9876543210 and gstin 37IHKXV5167L1ZS", phone="+919000000001")
    ref = next(l["ref"] for l in client.get("/api/partner/leads", headers=H).json() if l["phone"].endswith("0001"))
    text = " ".join(m["text"] for m in client.get(f"/api/partner/leads/{ref}/transcript", headers=H).json()["transcript"])
    assert "9876543210" not in text and "37IHKXV5167L1ZS" not in text


def test_chat_does_not_adopt_client_chosen_session_id():
    r = client.post("/api/chat", json={"session_id": "attacker-chosen", "message": "hi"}).json()
    assert r["session_id"] != "attacker-chosen"


def test_consent_and_kyc_need_existing_session():
    assert client.post("/api/consent", json={"session_id": "nope", "purposes": ["kyc_verification", "service_provisioning"], "accepted": True}).status_code == 404
    assert client.post("/api/kyc/verify", params={"session_id": "nope"}, files=[("files", ("a.png", b"x", "image/png"))]).status_code == 404


def test_kyc_upload_limits(monkeypatch):
    sid = client.post("/api/chat", json={"message": "hi"}).json()["session_id"]
    CONSENTS[sid] = {}
    monkeypatch.setattr(config, "KYC_MAX_BYTES", 10)
    r = client.post("/api/kyc/verify", params={"session_id": sid}, files=[("files", ("a.png", b"x" * 50, "image/png"))])
    assert r.status_code == 413
    monkeypatch.setattr(config, "KYC_MAX_FILES", 1)
    r = client.post("/api/kyc/verify", params={"session_id": sid}, files=[("files", ("a.png", b"x", "image/png")), ("files", ("b.png", b"x", "image/png"))])
    assert r.status_code == 413


def test_order_uses_server_profile_and_is_idempotent():
    sid = client.post("/api/chat", json={"message": "I run a clinic with 5 employees in Ludhiana"}).json()["session_id"]
    SESSIONS[sid].update(employees=5, sites=1, city_tier=2, segment="professional", needs=["internet"])
    from backend import rules
    rec = rules.recommend(__import__("backend.main", fromlist=["profile_from_state"]).profile_from_state(SESSIONS[sid]))
    items = rec["items"]
    KYC_RESULTS[sid] = {"verdict": "approved", "gst_status": "Active"}
    o1 = client.post("/api/order", json={"session_id": sid, "items": items, "employees": 500}).json()   # tampered headcount ignored
    o2 = client.post("/api/order", json={"session_id": sid, "items": items}).json()
    assert o1["order_id"] == o2["order_id"]
    assert SESSIONS[sid]["stage"] == "activated"
    ids = [l["ref"] for l in client.get("/api/partner/leads", headers=H).json()]
    assert all(l not in ids for l in [__import__("backend.main", fromlist=["lead_ref"]).lead_ref(sid)])   # activated leads leave the list


def test_stage_never_regresses_and_kyc_failure_sets_reason():
    from backend.main import touch
    st = {"stage": "quote_accepted"}
    touch(st, "contacted")
    assert st["stage"] == "quote_accepted"


def test_scoring_stage_and_profile_effects():
    base = dict(segment="micro", city_tier=2, employees=3, sites=1, has_gstin=False, channel="web", lang="en")
    scores = [scoring.score_lead(base, s)["probability"] for s in scoring.STAGES[:-1]]
    assert scores == sorted(scores) and scores[-1] > 2 * scores[0]          # later stage -> higher probability
    g = scoring.score_lead({**base, "has_gstin": True}, "needs_assessed")["probability"]
    assert g > scoring.score_lead(base, "needs_assessed")["probability"]
    s = scoring.score_lead(base, "lead")
    assert 0 <= s["score"] <= 100 and s["priority"] in ("High", "Medium", "Low")


def test_scoring_falls_back_without_model(monkeypatch):
    monkeypatch.setattr(scoring, "get_artifact", lambda: None)
    s = scoring.score_lead({"has_gstin": True}, "contacted")
    assert s["model"] == "heuristic" and s["reasons"] == ["+ GSTIN"]


def test_partner_html_assets_escape_output():
    js = client.get("/static/partner.js").text
    assert "const esc" in js and "${lead." not in js and "innerHTML = leads.map" in js
    assert "<script>alert" not in client.get("/whatsapp").text