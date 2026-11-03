"""Loads the organiser CSVs into memory and exposes lookups.

The case README only documents some catalogue columns, so every column is
resolved through `_pick()` which tries exact aliases first and then keyword
matching.  Run `python scripts/check_data.py` to see how your files resolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .config import DATA_DIR
from .gstin import normalise


def _pick(df: pd.DataFrame, aliases: list[str], keywords: list[list[str]] | None = None) -> str | None:
    cols = {c.lower().strip(): c for c in df.columns}
    for a in aliases:
        if a in cols:
            return cols[a]
    for kws in keywords or []:
        for lc, orig in cols.items():
            if all(k in lc for k in kws):
                return orig
    return None


def _num(v, default=0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return default
        return float(re.sub(r"[^\d.]", "", str(v)) or default)
    except ValueError:
        return default


def _split(v, sep="|") -> list[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return []
    return [x.strip() for x in re.split(r"[|;,]", str(v)) if x.strip()]


def seg_code(name: str) -> str:
    n = (name or "").lower()
    return ("micro" if "micro" in n else "professional" if "profession" in n else
            "multisite" if "multi" in n else "manufacturer" if "manufact" in n else "")


def _read_csv(path) -> pd.DataFrame:
    txt = path.read_text(encoding="utf-8-sig", errors="replace")
    if "<<<<<<<" in txt or ">>>>>>>" in txt:
        raise RuntimeError(f"{path.name} contains git merge-conflict markers. Re-copy the original from the organiser's pack.")
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")


@dataclass
class Product:
    id: str
    name: str
    category: str
    price_min: float
    price_max: float
    setup_fee: float
    contract_months: int
    tiers: list[int]
    min_employees: int
    min_sites: int
    requires_gstin: bool
    requires_one_of: list[str]
    install_days: int
    description: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def price(self) -> float:
        """Indicative monthly price: midpoint of the catalogue range."""
        return round((self.price_min + self.price_max) / 2)


class DataStore:
    def __init__(self) -> None:
        self.products: dict[str, Product] = {}
        self.gstin: dict[str, dict] = {}
        self.udyam: dict[str, dict] = {}
        self.col_report: dict[str, dict] = {}
        self.business_types: dict[str, str] = {}  # 'salon' -> 'micro'
        self.load()

    # ---------- loading ----------
    def load(self) -> None:
        self._load_catalogue()
        self._load_registry("mock_gstin_registry.csv", self.gstin, "gstin")
        self._load_registry("mock_udyam_registry.csv", self.udyam, "udyam")
        self._load_taxonomy()

    def _load_taxonomy(self) -> None:
        """Learn business_type -> segment from smb_leads.csv (optional file)."""
        path = DATA_DIR / "smb_leads.csv"
        if not path.exists():
            return
        df = _read_csv(path)
        if not {"business_type", "segment"} <= set(df.columns):
            return
        for bt, g in df.groupby("business_type"):
            code = seg_code(g["segment"].value_counts().idxmax())
            if bt.strip() and code:
                self.business_types[bt.strip().lower()] = code

    def _load_catalogue(self) -> None:
        path = DATA_DIR / "product_catalogue.csv"
        df = _read_csv(path)
        m = {
            "id": _pick(df, ["product_id", "id", "sku"], [["product", "id"]]),
            "name": _pick(df, ["product_name", "name"], [["name"]]),
            "category": _pick(df, ["category", "product_category", "type"], [["categ"]]),
            "price_min": _pick(df, ["monthly_price_min_inr", "price_min_inr"], [["price", "min"], ["monthly", "min"]]),
            "price_max": _pick(df, ["monthly_price_max_inr", "price_max_inr"], [["price", "max"], ["monthly", "max"]]),
            "setup": _pick(df, ["setup_fee_inr", "one_time_setup_inr"], [["setup"]]),
            "contract": _pick(df, ["contract_months", "min_contract_months"], [["contract"]]),
            "tiers": _pick(df, ["available_city_tiers"], [["tier"]]),
            "min_emp": _pick(df, ["min_employees"], [["min", "employ"]]),
            "min_sites": _pick(df, ["min_sites"], [["min", "site"]]),
            "req_gstin": _pick(df, ["requires_gstin"], [["gstin"]]),
            "req_one": _pick(df, ["requires_one_of"], [["requires", "one"]]),
            "install": _pick(df, ["typical_install_days"], [["install"]]),
            "desc": _pick(df, ["description", "plain_description"], [["desc"]]),
        }
        self.col_report["product_catalogue.csv"] = {"columns": list(df.columns), "resolved": m}
        if not m["id"]:
            raise RuntimeError(f"Could not find a product id column in {list(df.columns)}")
        for _, r in df.iterrows():
            g = lambda k: r[m[k]] if m[k] else ""
            pmin = _num(g("price_min"))
            pmax = _num(g("price_max")) or pmin
            tiers = [int(x) for x in re.findall(r"\d", str(g("tiers")))] or [1, 2]
            p = Product(
                id=str(g("id")).strip(),
                name=str(g("name")).strip() or str(g("id")),
                category=str(g("category")).strip(),
                price_min=pmin,
                price_max=pmax,
                setup_fee=_num(g("setup")),
                contract_months=int(_num(g("contract"), 12)),
                tiers=tiers,
                min_employees=int(_num(g("min_emp"))),
                min_sites=int(_num(g("min_sites"))),
                requires_gstin=str(g("req_gstin")).strip().upper() in ("Y", "YES", "TRUE", "1"),
                requires_one_of=_split(g("req_one")),
                install_days=int(_num(g("install"), 7)),
                description=str(g("desc")),
                raw=r.to_dict(),
            )
            self.products[p.id] = p

    def _load_registry(self, fname: str, target: dict, kind: str) -> None:
        df = _read_csv(DATA_DIR / fname)
        key = _pick(df, [kind, f"{kind}_number", f"{kind}_no"], [[kind]])
        name = _pick(df, ["legal_name", "enterprise_name", "business_name"], [["name"]])
        trade = _pick(df, ["trade_name"], [["trade"]])
        addr = _pick(df, ["address", "principal_address", "principal_place_of_business"], [["addr"], ["place"]])
        city = _pick(df, ["city"]); st_col = _pick(df, ["state"])
        status = _pick(df, ["status", "registration_status", "gst_status"], [["status"]])
        self.col_report[fname] = {"columns": list(df.columns),
                                  "resolved": {"key": key, "name": name, "trade": trade, "address": addr, "status": status}}
        for _, r in df.iterrows():
            rec = r.to_dict()
            rec["_name"] = r[name] if name else ""
            rec["_trade"] = r[trade] if trade else ""
            rec["_address"] = r[addr] if addr else ", ".join(x for x in (r[city] if city else "", r[st_col] if st_col else "") if x)
            rec["_status"] = (r[status] if status else "Active") or "Active"
            target[normalise(r[key])] = rec

    # ---------- lookups ----------
    def lookup_gstin(self, g: str) -> dict | None:
        return self.gstin.get(normalise(g))

    def lookup_udyam(self, u: str) -> dict | None:
        return self.udyam.get(normalise(u))


_store: DataStore | None = None


def get_store() -> DataStore:
    global _store
    if _store is None:
        _store = DataStore()
    return _store
