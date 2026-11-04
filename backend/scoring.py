# FILE: backend/scoring.py
import joblib
import pandas as pd
import numpy as np
from pathlib import Path
from .config import DATA_DIR
from .privacy import get_logger

log = get_logger("scoring")
MODEL_PATH = DATA_DIR / "propensity_model.joblib"

_model = None

def get_model():
    global _model
    if _model is None:
        try:
            _model = joblib.load(MODEL_PATH)
            log.info("Propensity ML model loaded successfully.")
        except Exception as e:
            log.warning(f"Could not load ML model (did you run train_propensity.py?): {e}")
    return _model

def predict_lead_score(profile_dict: dict) -> int:
    """Takes a dictionary of lead facts and returns a 0-100 probability score."""
    model = get_model()
    if not model:
        # Fallback heuristic score if the ML model is missing
        score = 20
        if profile_dict.get("has_gstin"): score += 40
        if profile_dict.get("city_tier") == 1: score += 15
        return min(score, 99)

    # Convert the single dictionary into a 1-row Pandas DataFrame for the model
    df = pd.DataFrame([{
        "segment": profile_dict.get("segment", "micro"),
        "city_tier": int(profile_dict.get("city_tier", 2)),
        "employee_count": int(profile_dict.get("employees", 1)),
        "has_gstin": 1 if profile_dict.get("has_gstin") else 0,
        "has_udyam": 1 if profile_dict.get("has_udyam") else 0,
        
        # Use np.nan so the SimpleImputer falls back to the dataset median
        "monthly_spend": profile_dict.get("monthly_spend") if profile_dict.get("monthly_spend") is not None else np.nan,
        
        "reached_contacted": 1,
        "reached_needs": 1 if profile_dict.get("reached_needs") else 0,
        "reached_quote": 1 if profile_dict.get("reached_quote") else 0
    }])
    
    # predict_proba returns [[prob_dropped, prob_activated]]
    probability = model.predict_proba(df)[0][1] 
    
    # Return as an integer score out of 100
    return int(round(probability * 100))