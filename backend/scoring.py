"""Lead propensity scoring for the back-office ("which leads should we call first?").

Design (validated on smb_leads.csv, see scripts/train_propensity.py):
  * A logistic-regression model learns how a lead's PROFILE (segment, city tier, team size, sites, GSTIN, channel,
    language) shifts its odds of activating.  Only facts the app can observe during a chat are used, so training and
    live inference see the same features.  Cross-validated AUC is ~0.71 and the model is calibrated.
  * The lead's FUNNEL STAGE sets the starting probability (empirical P(activate | stage) from the same file:
    7% at lead creation ... 63% after KYC).
  * Final score = stage prior, adjusted up/down by the profile (log-odds), so it is a real probability.
Funnel-stage flags are deliberately NOT model features: they are almost the label itself and would inflate accuracy.
"""
from __future__ import annotations

import math

import joblib
import numpy as np
import pandas as pd

from .config import DATA_DIR
from .privacy import get_logger

log = get_logger("scoring")
MODEL_PATH = DATA_DIR / "propensity_model.joblib"

STAGES = ["lead", "contacted", "needs_assessed", "quote_accepted", "kyc_complete", "activated"]
STAGE_LABEL = {"lead": "Lead", "contacted": "Contacted", "needs_assessed": "Needs assessed",
               "quote_accepted": "Quote accepted", "kyc_complete": "KYC complete", "activated": "Activated"}
# Empirical P(activate | stage reached) from smb_leads.csv; overwritten by the trained artifact when present.
DEFAULT_PRIOR = {"lead": 0.074, "contacted": 0.108, "needs_assessed": 0.186, "quote_accepted": 0.427, "kyc_complete": 0.626}
# Profile adds little once a lead is deep in the funnel (AUC 0.54 at KYC), so its adjustment is damped there.
PROFILE_WEIGHT = {"lead": 1.0, "contacted": 1.0, "needs_assessed": 1.0, "quote_accepted": 0.8, "kyc_complete": 0.3}

CATS = ["segment", "city_tier", "lead_source", "lang"]
NUMS = ["log_emp", "sites", "has_gstin"]

SEGMENT_NAME = {"micro": "Micro retail & services", "professional": "Professional offices",
                "multisite": "Small multi-site businesses", "manufacturer": "Small manufacturers"}
LANG_NAME = {"en": "English", "hi": "Hindi", "bn": "Bengali", "pa": "Punjabi", "gu": "Gujarati",
             "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam"}
CHANNEL_SOURCE = {"web": "Website form", "whatsapp": "WhatsApp campaign"}

HIGH, MEDIUM = 0.30, 0.12          # calibrated probability cut-offs for the priority label

_artifact: dict | None = None
_tried = False


def get_artifact() -> dict | None:
    global _artifact, _tried
    if not _tried:
        _tried = True
        try:
            _artifact = joblib.load(MODEL_PATH)
            log.info("Propensity model loaded (CV AUC %.3f)", _artifact["metrics"]["cv_auc"])
        except Exception as e:                       # missing file, version mismatch, ...
            log.warning("No trained propensity model (%s). Using stage-prior heuristic. Run: python scripts/train_propensity.py", e)
    return _artifact


def reset_cache() -> None:
    global _artifact, _tried
    _artifact, _tried = None, False


def model_info() -> dict:
    a = get_artifact()
    if not a:
        return {"model": "heuristic", "detail": "Stage prior + GSTIN bump (train a model for the full score)"}
    m = a["metrics"]
    return {"model": "logistic-regression + stage prior", "cv_auc": round(m["cv_auc"], 3),
            "top_decile_lift": round(m["top_decile_lift"], 2), "trained_on": m["n"], "base_rate": round(a["base_rate"], 4)}


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def features_from_profile(p: dict) -> pd.DataFrame:
    """Map what the app knows about a lead onto the training features (names must match train_propensity.py)."""
    seg = p.get("segment") or ""
    return pd.DataFrame([{
        "segment": SEGMENT_NAME.get(seg, seg or "unknown"),
        "city_tier": str(int(p.get("city_tier") or 2)),
        "lead_source": CHANNEL_SOURCE.get(p.get("channel", "web"), "Website form"),
        "lang": LANG_NAME.get(p.get("lang", "en"), "English"),
        "log_emp": float(np.log1p(int(p.get("employees") or 1))),
        "sites": int(p.get("sites") or 1),
        "has_gstin": 1 if p.get("has_gstin") else 0,
    }])


def _pretty(feature: str) -> str:
    kind, _, name = feature.partition("__")
    if kind == "c":
        for c in CATS:
            if name.startswith(c + "_"):
                val = name[len(c) + 1:]
                return {"segment": f"Segment: {val}", "city_tier": f"Tier {val} city",
                        "lead_source": f"Source: {val}", "lang": f"Language: {val}"}[c]
    return {"log_emp": "Team size", "sites": "Number of sites", "has_gstin": "GSTIN"}.get(name, name)


def _reasons(a: dict, df: pd.DataFrame, k: int = 3) -> list[str]:
    pre, clf = a["pipeline"].named_steps["p"], a["pipeline"].named_steps["m"]
    x = pre.transform(df)
    x = x.toarray()[0] if hasattr(x, "toarray") else np.asarray(x)[0]
    contrib = clf.coef_[0] * (x - a["feat_mean"])             # effect vs an average lead
    names = pre.get_feature_names_out()
    out = []
    for i in np.argsort(-np.abs(contrib)):
        if str(names[i]).startswith("c__") and x[i] == 0:      # a category the lead does NOT have is not a reason
            continue
        if abs(contrib[i]) > 0.02:
            out.append(f"{'+' if contrib[i] > 0 else '-'} {_pretty(names[i])}")
        if len(out) == k:
            break
    return out


def score_lead(profile: dict, stage: str = "needs_assessed") -> dict:
    """profile keys: segment(code), city_tier, employees, sites, has_gstin, channel(web|whatsapp), lang."""
    stage = stage if stage in DEFAULT_PRIOR else "needs_assessed"
    a = get_artifact()
    prior = (a or {}).get("stage_prior", DEFAULT_PRIOR).get(stage, DEFAULT_PRIOR[stage])
    if a:
        df = features_from_profile(profile)
        p_prof = float(a["pipeline"].predict_proba(df)[0][1])
        adj = _logit(p_prof) - _logit(a["base_rate"])
        p = _sigmoid(_logit(prior) + PROFILE_WEIGHT[stage] * adj)
        reasons, model = _reasons(a, df), "model"
    else:                                                    # model missing: stage prior + simple GSTIN bump
        p = _sigmoid(_logit(prior) + (0.5 if profile.get("has_gstin") else 0.0))
        reasons, model = (["+ GSTIN"] if profile.get("has_gstin") else []), "heuristic"
    priority = "High" if p >= HIGH else "Medium" if p >= MEDIUM else "Low"
    return {"score": int(round(p * 100)), "probability": round(p, 4), "priority": priority,
            "reasons": reasons, "model": model, "stage_prior": round(prior, 3)}


def predict_lead_score(profile_dict: dict) -> int:
    """Backwards-compatible helper: returns just the 0-100 score."""
    return score_lead(profile_dict, profile_dict.get("stage", "needs_assessed"))["score"]