import io
import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# -----------------------------------------------------------------------------
# 1. PAGE CONFIGURATION & TITLE
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="GoldLens - Mech Dynasty",
    page_icon="🪙",
    layout="wide"
)

st.title("🪙 GoldLens: Multi-Contract Gold Relative Value & Arbitrage Engine")
st.markdown("""
**Team Mech Dynasty** (Leader: Koyel Patra | Members: Aritra Ghosh, Sibam Mondal)  
*Same gold, different price: measure it, test it, trust it.* This prototype normalizes MCX gold settlement data into **₹/gram (100% fine gold equivalent)**, models relative pricing gaps using rolling Z-scores, and validates performance after real transaction costs.
""")

# -----------------------------------------------------------------------------
# 2. SIDEBAR CONTROLS & HYPERPARAMETERS
# -----------------------------------------------------------------------------
st.sidebar.header("⚙️ Strategy Hyperparameters")

window_size = st.sidebar.slider("Rolling Window Size (Days)", min_value=3, max_value=20, value=5)
z_entry = st.sidebar.slider("Z-Score Entry Threshold", min_value=1.0, max_value=2.5, value=1.5, step=0.1)
z_exit = st.sidebar.slider("Z-Score Exit Threshold", min_value=0.0, max_value=0.5, value=0.2, step=0.05)
# Charged on BOTH legs (GOLDTEN + GOLDM) for every unit of position traded.
cost_bps = st.sidebar.number_input("Transaction Cost per Leg (bps)", min_value=0.0, max_value=20.0, value=5.0, step=0.5) / 10000.0

st.sidebar.markdown("---")
uploaded_files = st.sidebar.file_uploader("Upload MCX BhavCopy CSV Files", type=["csv"], accept_multiple_files=True)

# Contract Specifications (quote unit in grams, purity as fineness out of 1000)
UNITS = {"GOLDM": 10.0, "GOLDTEN": 10.0, "GOLDGUINEA": 8.0, "GOLDPETAL": 1.0}
PURITY = {"GOLDM": 995.0, "GOLDTEN": 999.0, "GOLDGUINEA": 999.0, "GOLDPETAL": 999.0}
LEGS = ("GOLDM", "GOLDTEN")  # the two contracts whose spread is traded

# -----------------------------------------------------------------------------
# 3. DATA PROCESSING & NORMALIZATION PIPELINE
# -----------------------------------------------------------------------------
# Header variants that are accepted for each column (compared in lower case, letters/digits only)
COLUMN_ALIASES = {
    "Date": ["date", "tradedate", "tradingdate"],
    "Symbol": ["symbol"],
    "Close": ["close", "closeprice", "closingprice"],
    "Expiry": ["expirydate", "expiry", "expdate"],
    "Instrument": ["instrumentname", "instrument", "instrumenttype"],
    "OptionType": ["optiontype"],
}

DATE_FORMATS = (
    "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d%b%Y",
    "%d-%b-%y", "%d-%B-%Y", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y",
)


def standardize_columns(df):
    """Rename header variants (case, spaces, BOM) to Date / Symbol / Close / Expiry / ..."""
    lookup = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in df.columns}
    rename = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                rename[lookup[alias]] = canonical
                break
    return df.rename(columns=rename)


def parse_dates(series):
    """Parse a date column; cells that cannot be read become NaT instead of raising an error."""
    s = series.astype(str).str.strip()
    best = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    best_count = 0
    for fmt in DATE_FORMATS:  # keep the format that reads the most rows (day-first wins ties)
        parsed = pd.to_datetime(s, format=fmt, errors="coerce")
        count = int(parsed.notna().sum())
        if count > best_count:
            best, best_count = parsed, count
    if best.isna().any():  # leftovers: let pandas guess
        best = best.fillna(pd.to_datetime(s, errors="coerce"))
    return best


def read_bhavcopy(file):
    """Read one uploaded CSV (any encoding, optional title lines above the header)."""
    file.seek(0)
    raw = file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    lines = text.splitlines()
    start = next(
        (i for i, ln in enumerate(lines[:30]) if "symbol" in ln.lower() and "close" in ln.lower()),
        0,
    )
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype=str, skipinitialspace=True)
    return standardize_columns(df)


def load_uploads(files):
    """Read every uploaded file; unusable files are reported and skipped."""
    frames = []
    for f in files:
        try:
            df = read_bhavcopy(f)
        except Exception as exc:  # empty / unreadable file
            st.warning(f"Skipped '{f.name}': could not read it ({exc}).")
            continue
        missing = [c for c in ("Date", "Symbol", "Close") if c not in df.columns]
        if missing:
            st.warning(
                f"Skipped '{f.name}': missing column(s) {', '.join(missing)}. "
                f"Columns found: {', '.join(map(str, df.columns))}."
            )
            continue
        df["Date"] = parse_dates(df["Date"])
        if "Expiry" in df.columns:
            df["Expiry"] = parse_dates(df["Expiry"])
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def clean_prices(df):
    """Keep valid, positive gold *futures* prices (drops options, zero/blank closes, other symbols)."""
    df = df.copy()
    df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
    df["Close"] = pd.to_numeric(df["Close"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    df["Date"] = df["Date"].dt.normalize()
    if "Instrument" in df.columns:  # MCX futures are 'FUTCOM'; options must not be mixed in
        is_future = df["Instrument"].astype(str).str.strip().str.upper().str.startswith("FUT", na=False)
        if is_future.any():
            df = df[is_future]
    if "OptionType" in df.columns:
        df = df[~df["OptionType"].astype(str).str.strip().str.upper().isin(["CE", "PE"])]
    keep = df["Symbol"].isin(list(UNITS)) & df["Date"].notna() & (df["Close"] > 0)
    return df[keep].copy()


def select_contracts(df):
    """Pick the contract rows used each day. Returns (rows, note).

    MCX lists several expiries per symbol on every date, and GOLDM / GOLDTEN expire on
    different days. Averaging expiries (or pairing different months) distorts the spread,
    so both legs are taken from the SAME expiry month: the nearest month in which GOLDM
    and GOLDTEN both trade. If a date has no common month, each symbol's nearest expiry
    is used for that date. Without an expiry column, rows are averaged per symbol per day.
    """
    if "Expiry" in df.columns and df["Expiry"].notna().mean() >= 0.9:
        live = df[df["Expiry"].notna() & (df["Expiry"] >= df["Date"])].copy()
        if not live.empty:
            live["ExpKey"] = live["Expiry"].dt.year * 12 + live["Expiry"].dt.month
            live = live.sort_values(["Date", "Symbol", "Expiry"])
            live["Nearest"] = live.groupby(["Date", "Symbol"]).cumcount() == 0

            legs = live[live["Symbol"].isin(LEGS)]
            both = legs.groupby(["Date", "ExpKey"])["Symbol"].nunique()
            both = both[both == len(LEGS)].reset_index()
            target = both.groupby("Date")["ExpKey"].min()
            live["TargetKey"] = live["Date"].map(target)

            same_month = live["ExpKey"] == live["TargetKey"]
            no_common_month = live["TargetKey"].isna() & live["Nearest"]
            note = "Contract matching: futures only, GOLDM and GOLDTEN taken from the same expiry month."
            return live[same_month | no_common_month].copy(), note
    return df.copy(), "No usable expiry column found: all rows are averaged per symbol per day."


if not uploaded_files:
    st.info("👈 Please upload your MCX BhavCopy CSV files using the sidebar file uploader to launch the dashboard.")
    st.stop()

df_raw = load_uploads(uploaded_files)
if df_raw is None:
    st.error("None of the uploaded files could be used. Each CSV needs 'Date', 'Symbol' and 'Close' columns.")
    st.stop()

df_clean = clean_prices(df_raw)
df_filtered, contract_note = select_contracts(df_clean)
if df_filtered.empty:
    st.warning("No valid GOLDM / GOLDTEN / GOLDGUINEA / GOLDPETAL futures prices were found in the uploaded files.")
    st.stop()

# Normalize to ₹/g 100% Fine Gold
df_filtered["Unit"] = df_filtered["Symbol"].map(UNITS)
df_filtered["Purity"] = df_filtered["Symbol"].map(PURITY)
df_filtered["Norm_Price"] = (df_filtered["Close"] / df_filtered["Unit"]) * (1000.0 / df_filtered["Purity"])

# Pivot Dataset Side-by-Side by Date
df_pivot = df_filtered.pivot_table(index="Date", columns="Symbol", values="Norm_Price", aggfunc="mean").sort_index()
df_pivot.columns.name = None

if not set(LEGS).issubset(df_pivot.columns):
    st.warning("Uploaded files must contain both 'GOLDM' and 'GOLDTEN' contract symbols for spread analysis.")
    st.stop()

df_pivot = df_pivot.dropna(subset=list(LEGS)).copy()
if df_pivot.empty:
    st.warning("GOLDM and GOLDTEN have no trading dates in common in the uploaded files.")
    st.stop()
if len(df_pivot) <= window_size:
    st.warning(
        f"Only {len(df_pivot)} trading day(s) have both GOLDM and GOLDTEN, but the rolling window is "
        f"{window_size} days. Upload more files or reduce the window size."
    )
    st.stop()

st.caption(
    f"{contract_note} Using {len(df_pivot)} trading days "
    f"({df_pivot.index.min():%d %b %Y} to {df_pivot.index.max():%d %b %Y})."
)

# Calculate Spread and Z-Score
df_pivot['Spread'] = df_pivot['GOLDTEN'] - df_pivot['GOLDM']
rolling_spread = df_pivot['Spread'].rolling(window=window_size)
df_pivot['Mean'] = rolling_spread.mean()
df_pivot['Std'] = rolling_spread.std().clip(lower=0.0001)  # floor avoids division by ~0
df_pivot['Z_Score'] = (df_pivot['Spread'] - df_pivot['Mean']) / df_pivot['Std']

# -----------------------------------------------------------------------------
# 4. WALK-FORWARD BACKTEST & COST FRICTION ENGINE
# -----------------------------------------------------------------------------
positions = []
current_pos = 0

for z in df_pivot['Z_Score']:
    if pd.isna(z):
        current_pos = 0
    elif z > z_entry:
        current_pos = -1  # Short Spread (Sell GOLDTEN / Buy GOLDM)
    elif z < -z_entry:
        current_pos = 1   # Long Spread (Buy GOLDTEN / Sell GOLDM)
    elif abs(z) < z_exit:
        current_pos = 0   # Flat / Exit
    positions.append(current_pos)  # otherwise keep holding the current position

df_pivot['Position'] = positions
df_pivot['Spread_Change'] = df_pivot['Spread'].diff().fillna(0)
# Position taken at yesterday's close earns today's spread change (no look-ahead)
df_pivot['Gross_PnL'] = df_pivot['Position'].shift(1).fillna(0) * df_pivot['Spread_Change']

# Deduct transaction fees on position change: a flip from -1 to +1 trades 2 units, and every
# unit trades two legs (GOLDTEN and GOLDM). If your report charges one fee per spread trade,
# replace (GOLDM + GOLDTEN) with GOLDM below.
df_pivot['Pos_Change'] = df_pivot['Position'].diff().fillna(df_pivot['Position'])
df_pivot['Cost'] = df_pivot['Pos_Change'].abs() * (df_pivot['GOLDM'] + df_pivot['GOLDTEN']) * cost_bps
df_pivot['Net_PnL'] = df_pivot['Gross_PnL'] - df_pivot['Cost']

df_pivot['Cum_Gross'] = df_pivot['Gross_PnL'].cumsum()
df_pivot['Cum_Net'] = df_pivot['Net_PnL'].cumsum()

# Summary Metrics
tot_gross = df_pivot['Gross_PnL'].sum()
tot_cost = df_pivot['Cost'].sum()
tot_net = df_pivot['Net_PnL'].sum()
gold_beta = ((df_pivot['GOLDM'].iloc[-1] - df_pivot['GOLDM'].iloc[0]) / df_pivot['GOLDM'].iloc[0]) * 100

# -----------------------------------------------------------------------------
# 5. DASHBOARD METRICS DISPLAY
# -----------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Gross Strategy Alpha", f"₹{tot_gross:.2f} /g")
col2.metric("Transaction Drag", f"₹{tot_cost:.2f} /g")
col3.metric("Net Strategy Alpha", f"₹{tot_net:.2f} /g", delta=f"{tot_net:.2f}")
col4.metric("Underlying Gold Shift (Beta)", f"{gold_beta:+.2f}%")

st.markdown("---")

# -----------------------------------------------------------------------------
# 6. PLOTLY CHARTS
# -----------------------------------------------------------------------------
tab1, tab2, tab3 = st.tabs(["Cumulative Performance", "Normalized Prices", "Spread & Z-Score"])

with tab1:
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(x=df_pivot.index, y=df_pivot['Cum_Gross'], name='Gross PnL (₹/g)', line=dict(color='gray', dash='dash')))
    fig1.add_trace(go.Scatter(x=df_pivot.index, y=df_pivot['Cum_Net'], name='Net PnL (₹/g)', line=dict(color='green', width=2)))
    fig1.update_layout(title="Cumulative Strategy Returns (Gross vs Net Alpha)", xaxis_title="Date", yaxis_title="PnL (₹/g)")
    st.plotly_chart(fig1)

with tab2:
    price_cols = [c for c in ['GOLDM', 'GOLDTEN', 'GOLDGUINEA', 'GOLDPETAL'] if c in df_pivot.columns]
    fig2 = px.line(df_pivot, y=price_cols,
                   title="Normalized Contract Prices (100% Fine Gold Equivalent in ₹/g)")
    fig2.update_layout(xaxis_title="Date", yaxis_title="Normalized Price (₹/g)")
    st.plotly_chart(fig2)

with tab3:
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(x=df_pivot.index, y=df_pivot['Z_Score'], name='Z-Score', line=dict(color='purple')))
    fig3.add_hline(y=z_entry, line_dash="dash", line_color="red", annotation_text="Sell Threshold (+Z)")
    fig3.add_hline(y=-z_entry, line_dash="dash", line_color="green", annotation_text="Buy Threshold (-Z)")
    fig3.update_layout(title="Rolling Z-Score & Trading Thresholds", xaxis_title="Date", yaxis_title="Z-Score")
    st.plotly_chart(fig3)

# Data Export Button
st.download_button(
    label="📥 Download Backtest Results CSV",
    data=df_pivot.to_csv().encode('utf-8'),
    file_name="GoldLens_Backtest_Results.csv",
    mime="text/csv"
)
