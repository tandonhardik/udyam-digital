"""Run first: shows how your real CSV columns were mapped. Fix aliases in backend/data_store.py if any say None."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.data_store import get_store
s = get_store()
for f, r in s.col_report.items():
    print(f"\n== {f}\ncolumns : {r['columns']}")
    for k, v in r["resolved"].items():
        print(f"  {k:10s} -> {v}{'   <-- NOT FOUND' if v is None else ''}")
print(f"\nproducts={len(s.products)} gstin={len(s.gstin)} udyam={len(s.udyam)}")
for p in list(s.products.values())[:3]:
    print(json.dumps({k: v for k, v in p.__dict__.items() if k != 'raw'}, ensure_ascii=False))
statuses = {}
for r in s.gstin.values(): statuses[r['_status']] = statuses.get(r['_status'], 0) + 1
print("GSTIN statuses:", statuses)

# ---- smb_leads.csv (not used by the core app; summarised so the advisor/deck can be tuned to it) ----
import pandas as pd
from backend.config import DATA_DIR
p = DATA_DIR / "smb_leads.csv"
if not p.exists():
    print("\n[!] smb_leads.csv not found in", DATA_DIR, "(copy all 4 CSVs into data/)")
else:
    d = pd.read_csv(p, dtype=str).fillna("")
    print(f"\n== smb_leads.csv  rows={len(d)}\ncolumns : {list(d.columns)}")
    for c in ("segment", "business_type", "city_tier", "stage_reached", "outcome", "drop_reason", "lead_source", "preferred_language"):
        if c in d:
            print(f"\n{c}:\n{d[c].value_counts().head(12).to_string()}")
    if {"segment", "bundle_products"} <= set(d.columns):
        act = d[d["bundle_products"] != ""]
        print("\nTop bundles per segment (activated leads):")
        for seg, g in act.groupby("segment"):
            top = g["bundle_products"].str.replace(r"x\d+", "", regex=True).value_counts().head(3)
            print(f"  {seg}: " + " | ".join(f"{k} ({v})" for k, v in top.items()))
