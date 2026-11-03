"""Runs only when the organiser's real CSVs are in data/. Property test: the engine never sells a rule-breaking bundle."""
import os
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "data"
pytestmark = pytest.mark.skipif(not (DATA / "smb_leads.csv").exists() or "UDYAM_DATA_DIR" in os.environ and
                                Path(os.environ["UDYAM_DATA_DIR"]).name == "fixtures", reason="real data not present")

import pandas as pd

from backend import rules
from backend.data_store import get_store, seg_code
from backend.gstin import validate_gstin, validate_udyam

NEED_OF = {pid: need for need, ids in rules.NEED_PRODUCTS.items() for pid in ids}


def leads():
    return pd.read_csv(DATA / "smb_leads.csv", dtype=str).fillna("")


def test_registries_are_internally_valid():
    s = get_store()
    assert all(validate_gstin(g)[0] for g in s.gstin)
    assert all(validate_udyam(u)[0] for u in s.udyam)


def test_engine_never_breaks_rules_on_all_5000_leads():
    s, bad, sold = get_store(), [], 0
    for _, r in leads().iterrows():
        gst = s.lookup_gstin(r.gstin) if r.gstin else None
        prof = rules.Profile(employees=int(r.employee_count), sites=int(r.num_sites), city_tier=int(r.city_tier),
                             gstin_status=gst["_status"] if gst else None, segment=seg_code(r.segment),
                             needs=list(dict.fromkeys(NEED_OF[p] for p in r.products_of_interest.split(";") if p in NEED_OF)))
        rec = rules.recommend(prof)
        if rec["items"]:
            sold += 1
            if not rec["quote"]["valid"]:
                bad.append((r.lead_id, rec["quote"]["violations"]))
    assert not bad, bad[:3]
    assert sold > 4000


def test_historical_misselling_exists_in_activated_leads():
    """Business-case fact: the field-sales bundles in the data break catalogue rules; our engine cannot."""
    s, broken = get_store(), 0
    A = leads()
    A = A[A.outcome == "Activated"]
    for _, r in A.iterrows():
        ids = {x.split("x")[0] for x in r.bundle_products.split(";")}
        prof = rules.Profile(employees=int(r.employee_count), sites=int(r.num_sites), city_tier=int(r.city_tier),
                             gstin_status="Active" if r.has_gstin == "Y" else None)
        broken += bool(rules.validate_bundle(sorted(ids), prof))
    assert broken > 0
    print(f"{broken} of {len(A)} activated bundles break catalogue rules")


def test_k01_documents_end_to_end():
    """K01 specimen set: must be rejected for exactly the two planted problems."""
    import glob
    from backend import kyc
    files = sorted(glob.glob(str(DATA.parent / "kyc_documents" / "K01_*.png")))
    if len(files) < 4:
        pytest.skip("K01 images not present")
    r = kyc.process_upload([(Path(f).name, open(f, "rb").read()) for f in files])
    codes = {i["code"] for i in r["issues"] if i["severity"] == "error"}
    assert r["verdict"] == "rejected" and codes == {"GSTIN_CANCELLED", "UDYAM_MISSING"}, r["issues"]
