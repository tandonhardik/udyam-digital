# FILE: scripts/train_propensity.py
import pandas as pd
import joblib
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODEL_PATH = DATA_DIR / "propensity_model.joblib"

def train():
    print("Loading smb_leads.csv...")
    df = pd.read_csv(DATA_DIR / "smb_leads.csv")
    
    # 1. Define Target: 1 if Activated, 0 if Dropped
    y = (df['outcome'].str.strip().str.lower() == 'activated').astype(int)
    
    # 2. Feature Engineering (The Secret Sauce)
    
    # Convert string booleans to integers
    df['has_gstin'] = df['has_gstin'].astype(str).str.upper().map({'Y': 1, 'YES': 1, 'TRUE': 1, '1': 1}).fillna(0)
    df['has_udyam'] = df['has_udyam'].astype(str).str.upper().map({'Y': 1, 'YES': 1, 'TRUE': 1, '1': 1}).fillna(0)
    
    # Clean numeric fields
    df['employee_count'] = pd.to_numeric(df['employee_count'], errors='coerce').fillna(1)
    df['city_tier'] = pd.to_numeric(df['city_tier'], errors='coerce').fillna(2)
    df['monthly_spend'] = pd.to_numeric(df['monthly_telecom_spend_inr'], errors='coerce').fillna(0)
    
    # Extract "Funnel Depth" - How far did they get before dropping?
    # If a date exists in these columns, they reached that stage.
    df['reached_contacted'] = df['contacted_date'].notna().astype(int)
    df['reached_needs'] = df['needs_assessed_date'].notna().astype(int)
    df['reached_quote'] = df['quote_accepted_date'].notna().astype(int)
    
    # Select our upgraded feature list
    features = [
        'segment', 'city_tier', 'employee_count', 'has_gstin', 
        'has_udyam', 'monthly_spend', 'reached_contacted', 
        'reached_needs', 'reached_quote'
    ]
    X = df[features]
    
    # 3. Build Preprocessing Pipeline
    numeric_features = [
        'city_tier', 'employee_count', 'has_gstin', 'has_udyam', 
        'monthly_spend', 'reached_contacted', 'reached_needs', 'reached_quote'
    ]
    categorical_features = ['segment']
    
    preprocessor = ColumnTransformer(
        transformers=[
            # Random Forests don't need StandardScaling, just handle missing values
            ('num', SimpleImputer(strategy='median'), numeric_features),
            ('cat', OneHotEncoder(handle_unknown='ignore'), categorical_features)
        ])
    
    # 4. Create and Train the Random Forest Model
    clf = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', RandomForestClassifier(
            n_estimators=100, 
            max_depth=5,              # Prevent overfitting
            class_weight='balanced',  # Crucial for the 7% activation rate
            random_state=42
        )) 
    ])
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    print("Training Random Forest model...")
    clf.fit(X_train, y_train)
    
    # Let's look at a better metric for imbalanced data: ROC-AUC or just balanced accuracy
    from sklearn.metrics import roc_auc_score, classification_report
    
    # Predict probabilities for the test set
    y_probs = clf.predict_proba(X_test)[:, 1]
    y_pred = clf.predict(X_test)
    
    auc = roc_auc_score(y_test, y_probs)
    print(f"\nModel trained! ROC-AUC Score: {auc:.3f} (closer to 1.0 is better)")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred))
    
    # 5. Save the model
    joblib.dump(clf, MODEL_PATH)
    print(f"\nModel saved to {MODEL_PATH}")

if __name__ == "__main__":
    train()