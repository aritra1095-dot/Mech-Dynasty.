# 🪙 GoldLens: Multi-Contract Gold Relative Value & Arbitrage Engine

**Team Mech Dynasty** (Leader: Koyel Patra | Members: Aritra Ghosh, Sibam Mondal)  
*Hack in Hills '26 Project Submission*

---

## 🚀 Overview
**GoldLens** is an advanced quantitative relative value and arbitrage engine designed for the Multi Commodity Exchange (MCX). It identifies and capitalizes on temporary pricing inefficiencies between different gold futures contracts (`GOLDM`, `GOLDTEN`, `GOLDGUINEA`, `GOLDPETAL`) that represent the exact same underlying asset.

---

## 📊 Core Features & Technical Workflow
* **Universal ₹/Gram Normalization:** Ingests raw MCX Bhavcopy CSV files and normalizes varied contract sizes and purities into a precise 100% fine gold equivalent.
* **Statistical Z-Score Arbitrage:** Computes rolling spreads and dynamic Z-scores to trigger automated buy and sell thresholds when spreads stretch past historical norms.
* **Real-World Friction Modeling:** Accounts for exchange fees, brokerage, and basis point (bps) slippage to ensure the strategy generates true **Net Strategy Alpha**.
* **Interactive Dashboard:** Built using Python, Pandas, Plotly, and Streamlit, featuring live hyperparameter controls (rolling windows, entry/exit thresholds, and transaction costs).

---

## 🛠️ Tech Stack
* **Language:** Python
* **Framework:** Streamlit (Cloud Deployed)
* **Data Processing & Analytics:** Pandas, NumPy
* **Visualization:** Plotly

---

## ⚙️ How to Run Locally

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/aritra1095-dot/Goldlens.-app.git](https://github.com/aritra1095-dot/Goldlens.-app.git)
   cd Goldlens.-app
   pip install -r requirements.txt
   streamlit run app.py


   website public link - https://kzdsmc9hr2dhy37dgjfgry.streamlit.app/
