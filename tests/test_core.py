"""Runs against tests/fixtures (tiny FAKE csvs) so it works without the organiser pack.
Re-run with your real data:  UDYAM_DATA_DIR=data pytest -q  (fixture-specific ids will then skip)."""
import os
from datetime import date
from pathlib import Path

os.environ.setdefault("UDYAM_DATA_DIR", str(Path(__file__).parent / "fixtures"))

import pytest
from fastapi.testclient import TestClient

from backend import kyc, rules
from backend.gstin import gstin_checksum_char, validate_gstin, validate_udyam
from backend.main import app
from backend.privacy import mask_text

client = TestClient(app)
FIXTURE = Path(os.environ["UDYAM_DATA_DIR"]).name == "fixtures"
pytestmark = pytest.mark.skipif(not FIXTURE, reason="fixture-specific assertions")


def test_gstin_accepts_x_and_checks_checksum():
    assert validate_gstin("37IHKXV5167L1ZS")[0]
    assert not validate_gstin("37IHKXV5167L1ZT")[0]      # wrong checksum
    assert not validate_gstin("37IHKXV5167L1")[0]        # too short


def test_udyam_format():
    assert validate_udyam("UDYAM-AP-00-1234567")[0]
    assert not validate_udyam("UDYAM-AP-1234567")[0]


def prof(**kw):
    base = dict(employees=3, sites=1, city_tier=2, gstin_status=None)
    base.update(kw)
    return rules.Profile(**base)


def test_min_employees_and_gstin_and_tier():
    v = rules.validate_bundle(["P03"], prof(employees=3, city_tier=2))          # Biz Fibre 1G: tier1, >=5 emp, GSTIN
    text = " ".join(v["P03"])
    assert "5 employees" in text and "GSTIN" in text and "Tier 2" in text


def test_min_sites_for_sdwan():
    v = rules.validate_bundle(["P01", "P10"], prof(employees=20, sites=2, gstin_status="Active"))
    assert "P10" in v and "3 sites" in " ".join(v["P10"])


def test_dependency_enforced():
    assert "P23" in rules.validate_bundle(["P23"], prof())                      # CCTV needs a link
    assert not rules.validate_bundle(["P01", "P23"], prof())


def test_recommend_adds_link_for_cctv_and_prefers_fibre_over_airfiber():
    rec = rules.recommend(prof(segment="micro", needs=["cctv"]))
    ids = {i["product_id"] for i in rec["items"]}
    assert "P23" in ids and "P01" in ids and "P04" not in ids and rec["quote"]["valid"]


def test_leased_line_needs_gstin_and_is_explained():
    r = rules.recommend(prof(employees=40, city_tier=1, segment="manufacturer", needs=["internet"]))
    assert "P05" not in {i["product_id"] for i in r["items"]}
    assert any("GSTIN" in x for e in r["excluded"] for x in e["reasons"])
    r2 = rules.recommend(prof(employees=40, city_tier=1, segment="manufacturer", gstin_status="Active", needs=["internet"]))
    assert "P05" in {i["product_id"] for i in r2["items"]}


def test_quantities_follow_units():
    r = rules.recommend(prof(employees=6, sites=5, city_tier=2, segment="multisite", gstin_status="Active", needs=["internet", "email"]))
    q = {i["product_id"]: i["qty"] for i in r["items"]}
    assert q["P02"] == 5 and q["P16"] == 6                                      # per site / per user


def test_setup_fee_is_per_unit_and_contract_is_longest():
    q = rules.build_quote([{"product_id": "P23", "qty": 2}, {"product_id": "P01", "qty": 1}], prof())
    assert q["setup_total"] == 3600 and q["contract_months"] == 12


def test_suspended_gstin_blocks_gst_products():
    assert "P16" not in rules.validate_bundle(["P01", "P16"], prof(employees=8, gstin_status="Suspended"))  # P16 needs no GSTIN
    assert "P14" in rules.validate_bundle(["P14"], prof(employees=8, gstin_status="Suspended"))               # SMS needs GSTIN


def test_mask():
    assert "37IHKXV5167L1ZS" not in mask_text("gstin 37IHKXV5167L1ZS ok")


def docs(**over):
    d = {"gst": {"gstin": "37IHKXV5167L1ZS", "name": "Vindhya Tours & Travels", "address": "Shop 1, Tech Park Road, Visakhapatnam"},
         "udyam": {"udyam": "UDYAM-AP-00-1234567", "name": "Vindhya Tours & Travels"},
         "utility": {"name": "Vindhya Tours & Travels", "address": "Shop 1, Tech Park Road, Visakhapatnam", "bill_date": "2026-06-26"},
         "id": {"name": "Neha Kaur", "business": "Vindhya Tours & Travels", "valid_till": "2028-03-26"}}
    for k, v in over.items():
        d[k] = {**d[k], **v}
    return d


def codes(r):
    return {i["code"] for i in r["issues"]}


def test_kyc_happy_path():
    assert kyc.validate(docs())["verdict"] == "approved"


def test_kyc_stale_bill_uses_frozen_clock():
    r = kyc.validate(docs(utility={"bill_date": "2026-03-01"}))      # 136 days before 2026-07-15
    assert "BILL_STALE" in codes(r)
    assert "BILL_STALE" not in codes(kyc.validate(docs(utility={"bill_date": "2026-04-20"})))  # 86 days


def test_kyc_missing_udyam_number():
    assert "UDYAM_MISSING" in codes(kyc.validate(docs(udyam={"udyam": ""})))


def test_kyc_name_mismatch_and_bad_gstin_and_suspended():
    assert "NAME_MISMATCH" in codes(kyc.validate(docs(utility={"name": "Totally Different Co"})))
    assert "GSTIN_INVALID" in codes(kyc.validate(docs(gst={"gstin": "37IHKXV5167L1ZT"})))
    sus = "29ABCXD1234E1ZU"
    assert "GSTIN_SUSPENDED" in codes(kyc.validate(docs(gst={"gstin": sus, "name": "Ravi Traders"})))


def test_kyc_expired_id():
    assert "ID_EXPIRED" in codes(kyc.validate(docs(id={"valid_till": "2026-01-01"})))


def test_api_full_flow():
    r = client.post("/api/chat", json={"message": "I run a clinic with 5 employees in Ludhiana, need internet and email"}).json()
    sid = r["session_id"]
    while not r["done"]:
        ans = {"sites": "1", "gstin": "yes", "needs": "internet and email", "city": "Ludhiana", "employees": "5"}.get(
            __import__("backend.main", fromlist=["SESSIONS"]).SESSIONS[sid]["awaiting"], "yes")
        r = client.post("/api/chat", json={"session_id": sid, "message": ans}).json()
    items = r["recommendation"]["items"]
    # order blocked before consent/KYC
    assert client.post("/api/order", json={"session_id": sid, "items": items, "employees": 5}).status_code == 409
    assert client.post("/api/kyc/verify", params={"session_id": sid}, files=[("files", ("a.png", b"x", "image/png"))]).status_code == 403
    assert client.post("/api/consent", json={"session_id": sid, "purposes": ["kyc_verification"], "accepted": True}).status_code == 400
    # approve KYC directly to test order + tracker
    from backend.main import KYC_RESULTS
    KYC_RESULTS[sid] = {"verdict": "approved", "gst_status": "Active"}
    o = client.post("/api/order", json={"session_id": sid, "items": items, "employees": 5, "city_tier": 2}).json()
    t = client.get(f"/api/order/{o['order_id']}").json()
    assert t["current_stage"] == "KYC verified"
    t2 = client.get(f"/api/order/{o['order_id']}", params={"as_of": "2026-08-30"}).json()
    assert t2["activated"]


def test_lookup_endpoints():
    assert client.get("/api/lookup/gstin/37IHKXV5167L1ZS").json()["status"] == "Active"
    assert client.get("/api/lookup/gstin/37IHKXV5167L1ZT").status_code == 422


def test_groq_path_is_sanitised_and_falls_back(monkeypatch):
    from backend import advisor, llm
    monkeypatch.setattr(llm, "GROQ_API_KEY", "x")
    # model returns junk types + an unknown need -> only sane values survive
    monkeypatch.setattr(llm, "chat_json", lambda p: {"employees": "lots", "sites": 2, "needs": ["cctv", "rocket"], "city_tier": 9})
    out = advisor.llm_extract("hi", {})
    assert out == {"sites": 2, "needs": ["cctv"]}
    # LLM failure (None) -> fallback extractor still answers
    monkeypatch.setattr(llm, "chat_json", lambda p: None)
    state = {}
    r = advisor.step(state, "I run a kirana shop with 4 employees")
    assert state["employees"] == 4 and not r["done"]


def test_real_layout_registries_and_taxonomy():
    from backend.data_store import get_store
    s = get_store()
    assert "visakhapatnam" in s.lookup_gstin("37IHKXV5167L1ZS")["_address"].lower()
    assert "Visakhapatnam" in s.lookup_udyam("UDYAM-AP-00-1234567")["_address"]
    assert s.lookup_udyam("UDYAM-AP-00-1234567")["_status"] == "Active"   # no status column -> Active
    assert s.business_types["law office"] == "professional"


def test_script_language_detection_and_business_type():
    from backend import advisor
    assert advisor.detect_lang("मेरी दुकान") == "hi" and advisor.detect_lang("எனக்கு இணையம்") == "ta"
    assert advisor.detect_lang("hello") == "en"
    out = advisor.fallback_extract("I own a Salon with 4 employees", {})
    assert out["segment"] == "micro" and out["employees"] == 4


# ---- KYC parsing of the real specimen layout (OCR text captured from K01; no tesseract needed) ----
GST_OCR = """Specimen: GST Registration Certificate
GSTIN 37IHKXV5167L1ZS
Legal name Neha Kaur (Prop. Vindhya Tours & Travels)
Trade name Vindhya Tours & Travels
Constitution Proprietorship
Address of principal Shop/Unit 1, Tech Park Road, Visakhapatnam, A

place of business ndhra Pradesh --118383

Date of liability 2025-06-07
"""
ID_OCR = """Specimen: Authorised Signatory ID
Name
Neha Kaur
Designation
Office admin
For business
Vindhya Tours & Travels
ID no.
SYN-ID-346190
Valid till
2028-03-26
"""
UDYAM_OCR = """Specimen: Udyam Registration Certificate
Udyam reg. number

Enterprise name Vindhya Tours & Travels
Major activity Services
City Visakhapatnam
"""


def test_parse_table_layout_wrapped_address_and_stacked_labels():
    g = kyc.extract("gst", GST_OCR)
    assert g["gstin"] == "37IHKXV5167L1ZS" and g["name"] == "Vindhya Tours & Travels"
    assert g["address"] == "Shop/Unit 1, Tech Park Road, Visakhapatnam, Andhra Pradesh - 118383"   # mid-word wrap joined
    i = kyc.extract("id", ID_OCR)
    assert i == {"name": "Neha Kaur", "business": "Vindhya Tours & Travels", "valid_till": "2028-03-26"}
    u = kyc.extract("udyam", UDYAM_OCR)
    assert u["udyam"] == "" and u["name"] == "Vindhya Tours & Travels"                              # blank number is not swallowed


def test_gstin_ocr_repair():
    assert kyc.repair_gstin("37THKXV5167L1ZS") == ("37IHKXV5167L1ZS", True)       # I misread as T
    assert kyc.repair_gstin("37ITHKXV5167L1ZS") == ("37IHKXV5167L1ZS", True)      # stray inserted char
    assert kyc.repair_gstin("37IHKXV5167L1ZS") == ("37IHKXV5167L1ZS", False)
    assert not validate_gstin(kyc.repair_gstin("99ZZZZZ0000Z1ZZ")[0])[0]           # garbage stays invalid


def test_address_matching_is_wrap_tolerant():
    assert kyc.addr_match("Shop/Unit 1, Tech Park Road, Visakhapatnam, A ndhra Pradesh - 118383",
                          "Shop/Unit 1, Tech Park Road, Visakhapatnam, Andhra Pradesh - 118383")
    assert not kyc.addr_match("Shop 1, Tech Park Road, Visakhapatnam", "12 MG Road, Pune")


def test_month_name_dates():
    assert kyc._dates("Bill date 05 Jun 2026") == [date(2026, 6, 5)]
