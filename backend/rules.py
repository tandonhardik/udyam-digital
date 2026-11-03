"""Deterministic eligibility + quote engine. The LLM never decides what may be sold."""
from __future__ import annotations

from dataclasses import dataclass, field

from .data_store import Product, get_store

GST_RATE = 0.18

# Customer need -> catalogue products that can satisfy it (ordered by default preference).
NEED_PRODUCTS = {
    "internet": ["P01", "P02", "P03", "P05", "P06", "P04"],   # P04 (5G AirFiber) is the "no fibre" fallback
    "sim": ["P07", "P08"],
    "whatsapp": ["P15"], "sms": ["P14"], "telephony": ["P12", "P13"],
    "email": ["P16"], "backup": ["P17"], "website": ["P18"], "pos": ["P19"],
    "security": ["P20", "P21"], "cctv": ["P23"], "wifi": ["P22"],
    "failover": ["P09"], "sdwan": ["P10"], "staticip": ["P11"],
    "iot": ["P24"], "fleet": ["P25"],
}
# What each segment actually buys as its main link (from smb_leads.csv activated bundles).
INTERNET_PREF = {
    "micro": ["P01", "P04", "P02"],
    "professional": ["P02", "P01", "P03"],
    "multisite": ["P02", "P01"],
    "manufacturer": ["P05", "P03", "P06", "P02", "P01"],
}
INET = {"P01", "P02", "P03", "P04", "P05", "P06"}
SEG_TAG = {"micro": "micro", "professional": "prof", "multisite": "multi", "manufacturer": "mfg"}


@dataclass
class Profile:
    employees: int = 1
    sites: int = 1
    city_tier: int = 2
    gstin_status: str | None = None  # None = no GSTIN, else "Active"/"Suspended"/"Cancelled"
    segment: str = ""
    needs: list[str] = field(default_factory=list)

    @property
    def has_valid_gstin(self) -> bool:
        return (self.gstin_status or "").strip().lower() == "active"


def check_product(p: Product, prof: Profile, bundle_ids: set[str]) -> list[str]:
    """Return human-readable reasons the product cannot be sold (empty list = eligible)."""
    why = []
    if prof.city_tier not in p.tiers:
        why.append(f"{p.name} is not yet sold in Tier {prof.city_tier} cities")
    if prof.employees < p.min_employees:
        why.append(f"{p.name} needs at least {p.min_employees} employees (you have {prof.employees})")
    if prof.sites < p.min_sites:
        why.append(f"{p.name} needs at least {p.min_sites} sites (you have {prof.sites})")
    if p.requires_gstin and not prof.has_valid_gstin:
        state = prof.gstin_status or "no GSTIN"
        why.append(f"{p.name} needs a valid, active GSTIN (current status: {state})")
    if p.requires_one_of and not (set(p.requires_one_of) & bundle_ids):
        store = get_store()
        names = " or ".join(store.products[i].name if i in store.products else i for i in p.requires_one_of)
        why.append(f"{p.name} must be bought together with: {names}")
    return why


def validate_bundle(ids: list[str], prof: Profile) -> dict[str, list[str]]:
    store = get_store()
    bundle = set(ids)
    out = {}
    for pid in ids:
        p = store.products.get(pid)
        out[pid] = [f"Unknown product {pid}"] if not p else check_product(p, prof, bundle)
    return {k: v for k, v in out.items() if v}


def build_quote(items: list[dict], prof: Profile) -> dict:
    """items: [{"product_id": "P01", "qty": 1}]. Returns lines, totals, violations."""
    store = get_store()
    ids = [i["product_id"] for i in items]
    violations = validate_bundle(ids, prof)
    lines, mrc, setup, contract = [], 0.0, 0.0, 0
    for it in items:
        p = store.products.get(it["product_id"])
        if not p:
            continue
        qty = max(int(it.get("qty", 1)), 1)
        line_mrc, line_setup = p.price * qty, p.setup_fee * qty
        mrc += line_mrc
        setup += line_setup
        contract = max(contract, p.contract_months)
        lines.append({"product_id": p.id, "name": p.name, "category": p.category, "qty": qty, "unit": p.raw.get("unit", ""),
                      "unit_monthly": p.price, "monthly": line_mrc, "setup": line_setup,
                      "price_range": [p.price_min, p.price_max], "contract_months": p.contract_months,
                      "install_days": p.install_days})
    return {"lines": lines, "monthly_total": mrc, "gst_on_monthly": round(mrc * GST_RATE),
            "monthly_with_gst": round(mrc * (1 + GST_RATE)), "setup_total": setup,
            "contract_months": contract, "violations": violations, "valid": not violations and bool(lines)}


def candidates(need: str, prof: Profile) -> list[Product]:
    store = get_store()
    ids = list(NEED_PRODUCTS.get(need, []))
    if need == "internet":
        pref = [i for i in INTERNET_PREF.get(prof.segment, []) if i in ids]
        ids = pref + [i for i in ids if i not in pref]
    return [store.products[i] for i in ids if i in store.products]


def _blockers(p: Product, prof: Profile) -> list[str]:
    """Reasons a product can't be sold to this profile, ignoring bundle dependencies (resolved separately)."""
    return [w for w in check_product(p, prof, set(p.requires_one_of) | {p.id}) if "bought together" not in w]


def default_qty(p: Product, prof: Profile) -> int:
    unit = (p.raw.get("unit") or "").lower()
    if p.id == "P23":                         # cameras scale with sites
        return min(2 * prof.sites, 24)
    if unit in ("per site", "per counter"):
        return max(prof.sites, 1)
    if unit == "per line":
        return max(1, min(-(-prof.employees // 2), 6))
    if unit == "per user":
        return max(1, min(prof.employees, 50))
    if unit == "per device":
        return 8 if p.id == "P24" else max(2, min(prof.employees, 12))
    if unit == "per vehicle":
        return 5
    if unit == "per channel":
        return 3
    return 1


def recommend(prof: Profile) -> dict:
    """Choose the best eligible product per need, add/validate dependencies, explain anything left out."""
    store = get_store()
    chosen: dict[str, Product] = {}
    excluded: list[dict] = []

    for need in prof.needs:
        cands = candidates(need, prof)
        picked = None
        for p in cands:
            probs = _blockers(p, prof)
            if not probs:
                picked = p
                break
            if p is cands[0]:       # preferred product is blocked: tell the owner why (an upgrade path)
                excluded.append({"need": need, "product": p.name, "reasons": probs})
        if picked:
            chosen[picked.id] = picked
        elif cands and not any(e["product"] == cands[0].name for e in excluded):
            excluded.append({"need": need, "product": cands[0].name, "reasons": _blockers(cands[0], prof)})

    # Dependencies: add a main link if none exists; never sell two links just to satisfy a companion product.
    for p in list(chosen.values()):
        if not p.requires_one_of or set(p.requires_one_of) & set(chosen):
            continue
        have_link = set(chosen) & INET
        providers = [i for i in INTERNET_PREF.get(prof.segment, []) + sorted(INET) if i in p.requires_one_of]
        providers = [store.products[i] for i in dict.fromkeys(providers) if i in store.products and not _blockers(store.products[i], prof)]
        if not have_link and providers:
            chosen[providers[0].id] = providers[0]
        else:
            chosen.pop(p.id)
            names = " or ".join(store.products[i].name for i in p.requires_one_of if i in store.products)
            excluded.append({"need": "dependency", "product": p.name,
                             "reasons": [f"{p.name} works only with: {names}"]})

    items = [{"product_id": p.id, "qty": default_qty(p, prof)} for p in chosen.values()]
    quote = build_quote(items, prof)
    tag = SEG_TAG.get(prof.segment, "")
    addons = []
    for p in store.products.values():
        if p.id in chosen or p.id in INET and set(chosen) & INET:
            continue
        if not check_product(p, prof, set(chosen) | {p.id}):
            fit = tag and tag in (p.raw.get("target_segments") or "").split(";")
            addons.append((0 if fit else 1, p.price, {"product_id": p.id, "name": p.name, "category": p.category,
                           "monthly": p.price, "setup": p.setup_fee, "unit": p.raw.get("unit", ""), "qty": default_qty(p, prof)}))
    addons = [a[2] for a in sorted(addons, key=lambda a: a[:2])][:8]
    return {"items": items, "quote": quote, "excluded": excluded, "addons": addons}
