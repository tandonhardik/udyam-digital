# 🚀 Project Udyam Digital 

Hey there! Welcome to my repository for **Udyam Digital**, a smart telecom onboarding and KYC platform. 

When I started this hackathon, I wanted to build something that didn't just look good on the frontend, but actually worked like a real-world, production-grade system under the hood. I'm incredibly proud to share that this solution completes the **Core Flow and all three Stretch Goals**, while strictly enforcing DPDP Act data privacy.

Here is a breakdown of what I built and how to run it.

## 🏆 My Biggest Wins & Accomplishments

### 1. Bulletproof Core Flow (No AI Hallucinations)
I used Groq's LLM purely for intent extraction (understanding what the business owner wants). For the actual quoting, I built a strict, deterministic Python rules engine. It mathematically enforces catalogue dependencies, minimum employee counts, and GSTIN requirements, ensuring the AI can never misquote a customer. 

### 2. A "Smart" (and highly forgiving) KYC Engine
I didn't just mock the document uploads. I integrated **Tesseract OCR** with a custom **OpenCV deskewing algorithm** that actively straightens tilted document uploads before reading them. 
*   **Auto-Correction:** If the OCR misreads a character on a GSTIN (like confusing a "1" for an "I"), my backend mathematically tests the checksum, fixes the typo, and verifies it against the government registry. 
*   **Data Minimisation:** To comply with the DPDP Act, all uploaded images are processed entirely in memory as byte streams. Nothing is ever saved to the disk, and all PII is automatically masked in the terminal logs!

### 3. Hitting Every Stretch Goal
*   **Omnichannel WhatsApp:** I built a simulated WhatsApp interface. The coolest part? It uses **Session Hydration**, meaning a user can start chatting on WhatsApp, click a link, and immediately finish their KYC on the web app without losing their conversation context.
*   **ML Propensity Scoring:** I engineered a feature pipeline to track "funnel depth" and trained a Scikit-Learn **Random Forest** model (`train_propensity.py`) to predict the exact percentage likelihood of a lead activating. 
*   **Partner Portal:** I built a back-office dashboard for the sales team that dynamically ranks abandoned digital sessions based on their ML Propensity Score (High/Medium/Low) and allows reps to read the GenAI chat transcripts.

---

## 💻 How to Run This Locally (For the Judges)

If you are evaluating my project, here is exactly how to get it running on your machine.

### Step 1: Prerequisites
You'll need Python 3.10+ and the **Tesseract OCR engine** installed on your system.
*   **Ubuntu/WSL:** `sudo apt install tesseract-ocr`
*   **macOS:** `brew install tesseract`
*   **Windows:** Install from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and make sure it is added to your PATH.

### Step 2: Setup the Environment
Clone this repo, set up a virtual environment, and install my dependencies:
```bash
python -m venv .venv
source .venv/bin/activate      # On Windows use: .venv\Scripts\activate
pip install -r requirements.txt
```

### Step 3: Add the Secrets
Create a `.env` file in the root folder (I added this to `.gitignore` to keep my keys secure). Add your Groq API key:
```text
GROQ_API_KEY=gsk_your_actual_key_here
```
*(Note: If you skip this, no worries! The app will gracefully fall back to my offline Regex keyword extractor).*

### Step 4: Data & Machine Learning Setup
1. Copy the 4 CSV files provided by the organizers (`product_catalogue.csv`, `smb_leads.csv`, `mock_gstin_registry.csv`, `mock_udyam_registry.csv`) directly into the `data/` folder.
2. **Train the ML Model!** (I didn't push the heavy `.joblib` file to GitHub). Run my training script to ingest the 5,000 leads and generate the scoring model:
```bash
python scripts/train_propensity.py
```

### Step 5: Start the Server
Boot it up!
```bash
uvicorn backend.main:app --reload --port 8000
```

---

## 🎮 Exploring the App

Once the server is running, check out these three URLs to see the system in action:

1. **The Consumer Web App:** [http://localhost:8000/](http://localhost:8000/) 
   *(Chat with the bot, build a quote, and try uploading the K01 and K03 test images to see the KYC engine approve or reject them!)*
2. **The WhatsApp Simulator:** [http://localhost:8000/whatsapp](http://localhost:8000/whatsapp)
3. **The Sales Partner Portal:** [http://localhost:8000/partner](http://localhost:8000/partner)

---

## 🧪 Testing
If you want to verify the logic, you can run my test suites:
```bash
pytest tests/test_core.py -q           # Unit tests 
pytest tests/test_real_data.py -q -s   # Validates the engine against all 5,000 real leads!
```

---

## 🏗️ Architecture Layout
```text
backend/  
 ├── main.py        (FastAPI routes & hydration logic)
 ├── advisor.py     (Groq LLM intent extraction)
 ├── rules.py       (Deterministic eligibility & quote engine)
 ├── kyc.py         (Tesseract OCR, OpenCV deskew, strict registry cross-validation)
 ├── scoring.py     (Scikit-Learn Random Forest inference)
 └── privacy.py     (DPDP PII masking logger)
frontend/ 
 ├── app.js & index.html       (Main flow)
 ├── partner.js & partner.html (Back-Office)
 └── whatsapp.html             (WhatsApp Simulator)
scripts/  
 └── train_propensity.py       (Feature engineering & ML training pipeline)
```

Thank you for checking out my project!