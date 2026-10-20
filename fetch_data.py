import os
import sys
import json
import time
import math
from datetime import datetime, timezone

try:
    import yfinance as yf
except ImportError:
    print("FATAL: yfinance not installed. Run: pip install yfinance")
    sys.exit(1)

CONFIG_FILE = "config.json"
DATA_DIR = "data"
HISTORY_DIR = os.path.join(DATA_DIR, "history")
OUTPUT_DATA = os.path.join(DATA_DIR, "data.json")

def clean_val(val):
    if val is None:
        return None
    if isinstance(val, (int, str, bool)):
        return val
    if isinstance(val, float):
        return None if math.isnan(val) or math.isinf(val) else val
    return None

def fetch_ticker_data(ticker_symbol):
    max_retries = 3
    backoff = [3, 8]
    data = None
    last_err = None

    for attempt in range(max_retries):
        try:
            t = yf.Ticker(ticker_symbol)
            info = t.info
            if not info or len(info) < 5:
                raise ValueError("Incomplete info payload from Yahoo")
            data = {"info": info, "ticker": t}
            break
        except Exception as e:
            last_err = str(e)
            if attempt < max_retries - 1:
                sleep_sec = backoff[attempt]
                print(f"[{ticker_symbol}] Retry {attempt+1} after error: {last_err}. Sleeping {sleep_sec}s...")
                time.sleep(sleep_sec)
            else:
                print(f"[{ticker_symbol}] Failed after {max_retries} attempts: {last_err}")
                return None, last_err
        time.sleep(0.5)

    info = data["info"]
    t = data["ticker"]
    scalars = {}

    for k, v in info.items():
        if isinstance(v, (int, float, str, bool)):
            if k == "longBusinessSummary" and isinstance(v, str):
                scalars[k] = v[:600]
            else:
                clean = clean_val(v)
                if clean is not None:
                    scalars[k] = clean

    # Fallback keys for compatibility
    if "currentPrice" not in scalars and "regularMarketPrice" in scalars:
        scalars["currentPrice"] = scalars["regularMarketPrice"]
    if "fiftyTwoWeekChange" in info:
        scalars["fiftyTwoWeekChange"] = clean_val(info["fiftyTwoWeekChange"])
        scalars["alias_52WeekChange"] = scalars["fiftyTwoWeekChange"]

    def extract_financials(df, prefix):
        if df is None or df.empty:
            return
        try:
            cols = list(df.columns)
            for col_idx, col_date in enumerate(cols[:3]):
                date_str = str(col_date)[:10]
                scalars[f"{prefix}_date_{col_idx}"] = date_str
                for row_name in df.index:
                    clean_row = str(row_name).replace(" ", "").replace("-", "_")
                    val = clean_val(df.loc[row_name, col_date])
                    if val is not None:
                        scalars[f"{prefix}_{clean_row}_{col_idx}"] = val
        except Exception as fe:
            print(f"[{ticker_symbol}] Financials warning: {fe}")

    try:
        extract_financials(t.income_stmt, "a_is")
        extract_financials(t.quarterly_income_stmt, "q_is")
        extract_financials(t.balance_sheet, "a_bs")
        extract_financials(t.quarterly_balance_sheet, "q_bs")
        extract_financials(t.cashflow, "a_cf")
        extract_financials(t.quarterly_cashflow, "q_cf")
    except Exception as fe:
        print(f"[{ticker_symbol}] Statements warning: {fe}")

    # Fetch 5-year price history
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        hist = t.history(period="5y", interval="1d")
        if not hist.empty:
            dates = [d.strftime("%Y-%m-%d") for d in hist.index]
            closes = [round(float(c), 2) for c in hist["Close"]]
            hist_path = os.path.join(HISTORY_DIR, f"{ticker_symbol}.json")
            with open(hist_path, "w", encoding="utf-8") as hf:
                json.dump({"symbol": ticker_symbol, "dates": dates, "closes": closes}, hf, separators=(',', ':'))
    except Exception as he:
        print(f"[{ticker_symbol}] History error: {he}")

    return scalars, None

def main():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found.")
        sys.exit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    tickers = config.get("tickers", [])
    if not tickers:
        print("No tickers defined in config.json")
        sys.exit(0)

    os.makedirs(DATA_DIR, exist_ok=True)
    all_data = {}
    errors = {}
    success_count = 0

    print(f"Fetching data for {len(tickers)} tickers: {', '.join(tickers)}")
    for sym in tickers:
        sym_clean = sym.strip().upper()
        scalars, err = fetch_ticker_data(sym_clean)
        if scalars:
            all_data[sym_clean] = scalars
            success_count += 1
            print(f"-> SUCCESS: {sym_clean} ({len(scalars)} fields)")
        else:
            errors[sym_clean] = err
            print(f"-> FAIL: {sym_clean} - {err}")
        time.sleep(1.0)

    if success_count == 0 and len(tickers) > 0:
        print("FATAL: All tickers failed. Aborting without overwriting.")
        sys.exit(1)

    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "count": success_count,
        "tickers": all_data,
        "errors": errors
    }

    with open(OUTPUT_DATA, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Complete. Wrote {success_count} tickers to {OUTPUT_DATA}")

if __name__ == "__main__":
    main()
