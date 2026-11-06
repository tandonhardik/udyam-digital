"""Train the lead-propensity model from smb_leads.csv.      Run from the project folder:

    python scripts/train_propensity.py

What it does (and why):
  * Features are ONLY facts the app can see in a live chat (segment, city tier, team size, sites, GSTIN, channel,
    language), so training and inference match.
  * Funnel-stage flags (contacted / needs / quote reached) are NOT features. They are almost the label itself
    (everyone who activated passed every stage) and gave a misleading 0.96 AUC. The stage is used as a calibrated
    prior at scoring time instead (see backend/scoring.py).
  * Logistic regression is used over a random forest: it scored better (0.71 vs 0.69 CV AUC), is already
    calibrated, and can explain each score.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from backend.config import DATA_DIR
from backend.scoring import CATS, MODEL_PATH, NUMS

STAGE_COLS = {"contacted": "contacted_date", "needs_assessed": "needs_assessed_date",
              "quote_accepted": "quote_accepted_date", "kyc_complete": "kyc_complete_date"}


def yes(col: pd.Series) -> pd.Series:
    return col.astype(str).str.strip().str.upper().isin(["Y", "YES", "TRUE", "1"]).astype(int)


def build_frame(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "segment": df["segment"].fillna("unknown"),
        "city_tier": pd.to_numeric(df["city_tier"], errors="coerce").fillna(2).astype(int).astype(str),
        "lead_source": df["lead_source"].fillna("unknown"),
        "lang": df["preferred_language"].fillna("English"),
        "log_emp": np.log1p(pd.to_numeric(df["employee_count"], errors="coerce").fillna(1)),
        "sites": pd.to_numeric(df["num_sites"], errors="coerce").fillna(1),
        "has_gstin": yes(df["has_gstin"]),
    })


def train() -> None:
    path = DATA_DIR / "smb_leads.csv"
    print(f"Loading {path} ...")
    df = pd.read_csv(path)
    y = (df["outcome"].str.strip().str.lower() == "activated").astype(int).values
    X = build_frame(df)

    pre = ColumnTransformer([("c", OneHotEncoder(handle_unknown="ignore"), CATS), ("n", StandardScaler(), NUMS)])
    pipe = Pipeline([("p", pre), ("m", LogisticRegression(C=0.5, max_iter=2000))])

    # Honest evaluation: out-of-fold predictions, never scored on rows the model trained on.
    oof = cross_val_predict(pipe, X, y, cv=StratifiedKFold(5, shuffle=True, random_state=0), method="predict_proba")[:, 1]
    auc = roc_auc_score(y, oof)
    k = len(y) // 10
    top = np.argsort(-oof)[:k]
    lift = y[top].mean() / y.mean()
    print(f"\nCross-validated ROC-AUC (profile only): {auc:.3f}")
    print(f"Top-decile lift: top 10% of leads hold {100 * y[top].mean():.1f}% activations vs {100 * y.mean():.1f}% overall ({lift:.1f}x)")
    print(f"Mean predicted {100 * oof.mean():.1f}% vs actual {100 * y.mean():.1f}%  (calibration check)")

    stage_prior = {"lead": float(y.mean())}
    for stage, col in STAGE_COLS.items():
        stage_prior[stage] = float(y[df[col].notna().values].mean())
    print("\nP(activate | stage reached):  " + "   ".join(f"{s}: {100 * p:.1f}%" for s, p in stage_prior.items()))

    pipe.fit(X, y)
    feat_mean = pipe.named_steps["p"].transform(X)
    feat_mean = np.asarray(feat_mean.mean(axis=0)).ravel()
    artifact = {"pipeline": pipe, "base_rate": float(y.mean()), "stage_prior": stage_prior, "feat_mean": feat_mean,
                "metrics": {"cv_auc": float(auc), "top_decile_lift": float(lift), "n": int(len(y))},
                "sklearn": sklearn.__version__, "version": 2}
    joblib.dump(artifact, MODEL_PATH)
    print(f"\nModel saved to {MODEL_PATH}")


if __name__ == "__main__":
    train()