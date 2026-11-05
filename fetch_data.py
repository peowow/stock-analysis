import os
import sys
import json
import time
import math
import urllib.request
import csv
from datetime import datetime, timezone

try:
    import yfinance as yf
except ImportError:
    print("FATAL: yfinance not installed. Run: pip install yfinance")
    sys.exit(1)

CONFIG_FILE = "config.json"
DATA_DIR = "data"
OUTPUT_DATA = os.path.join(DATA_DIR, "data.json")

DAMODARAN_MAP = {
    "Semiconductors": "Semiconductor",
    "Semiconductor Equipment & Materials": "Semiconductor Equip",
    "Oil & Gas E&P": "Oil/Gas (Production and Exploration)",
    "Oil & Gas Integrated": "Oil/Gas (Integrated)",
    "Oil & Gas Refining & Marketing": "Oil/Gas (Refining and Marketing)",
    "Auto Manufacturers": "Auto & Truck",
    "Auto Parts": "Auto Parts",
    "Software - Infrastructure": "Software (System & Application)",
    "Software - Application": "Software (System & Application)",
    "Internet Content & Information": "Information Services",
    "Banks - Diversified": "Bank (Money Center)",
    "Banks - Regional": "Banks (Regional)",
    "Biotechnology": "Biotechnology",
    "Drug Manufacturers - General": "Drugs (Pharmaceutical)",
    "Consumer Electronics": "Computers/Peripherals",
    "Telecom Services": "Telecom (Wireless)",
    "Utilities - Regulated Electric": "Utility (General)"
}

def clean_val(val):
    if val is None:
        return None
    if isinstance(val, (int, str, bool)):
        return val
    if isinstance(val, float):
        return None if math.isnan(val) or math.isinf(val) else val
    return None

def fetch_damodaran_benchmarks():
    benchmarks = {}
    url_pedata = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/pedata.csv"
    url_margin = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/margin.csv"
    headers = {'User-Agent': 'Mozilla/5.0'}

    def parse_csv_stream(url):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                lines = [line.decode('latin-1') for line in resp.readlines()]
                return list(csv.reader(lines))
        except Exception:
            return []

    for r in parse_csv_stream(url_pedata):
        if len(r) > 8 and r[0].strip() and r[0].strip() != "Industry Name":
            def safe_float(idx):
                try: return float(r[idx].replace(',', '').replace('%', '').strip())
                except: return None
            benchmarks[r[0].strip()] = {
                "trailingPE": safe_float(4),
                "forwardPE": safe_float(5),
                "priceToBook": safe_float(6),
                "enterpriseToEbitda": safe_float(8)
            }

    for r in parse_csv_stream(url_margin):
        if len(r) > 6 and r[0].strip():
            ind = r[0].strip()
            def safe_float_pct(idx):
                try: return float(r[idx].replace(',', '').replace('%', '').strip())
                except: return None
            if ind in benchmarks:
                benchmarks[ind]["grossMargins"] = safe_float_pct(2)
                benchmarks[ind]["operatingMargins"] = safe_float_pct(4)
                benchmarks[ind]["profitMargins"] = safe_float_pct(5)

    return benchmarks

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
                raise ValueError("Incomplete info payload")
            data = {"info": info}
            break
        except Exception as e:
            last_err = str(e)
            if attempt < max_retries - 1:
                time.sleep(backoff[attempt])
            else:
                return None, last_err
        time.sleep(0.5)

    info = data["info"]
    scalars = {}

    for k, v in info.items():
        if isinstance(v, (int, float, str, bool)):
            clean = clean_val(v)
            if clean is not None:
                scalars[k] = clean

    # 1. SMART ACTIVE PRICING (Premarket / After-hours handling)
    market_state = str(info.get("marketState", "")).upper()
    reg_price = clean_val(info.get("regularMarketPrice")) or clean_val(info.get("currentPrice"))
    pre_price = clean_val(info.get("preMarketPrice"))
    post_price = clean_val(info.get("postMarketPrice"))

    active_price = reg_price
    if market_state in ["PRE", "PREPRE"] and pre_price:
        active_price = pre_price
    elif market_state in ["POST", "POSTPOST"] and post_price:
        active_price = post_price

    scalars["currentPrice"] = active_price
    scalars["regularMarketPrice"] = reg_price
    if pre_price: scalars["preMarketPrice"] = pre_price
    if post_price: scalars["postMarketPrice"] = post_price
    scalars["marketState"] = market_state

    # 2. ACCURATE DIVIDEND YIELD CALCULATION (Immune to decimal mismatch)
    div_rate = clean_val(info.get("dividendRate"))
    if div_rate and active_price and active_price > 0:
        scalars["dividendYield"] = round(div_rate / active_price, 6)
    elif "dividendYield" in info:
        raw_yield = clean_val(info["dividendYield"])
        if raw_yield is not None:
            # If Yahoo supplied e.g. 2.8 instead of 0.028, normalize it
            if raw_yield > 0.5:
                raw_yield = raw_yield / 100.0
            scalars["dividendYield"] = raw_yield

    # 3. ACCURATE PAYOUT RATIO CALCULATION
    if "payoutRatio" in info:
        raw_payout = clean_val(info["payoutRatio"])
        if raw_payout is not None:
            # If Yahoo supplied e.g. 18.0 instead of 0.18
            if raw_payout > 1.0 and raw_payout <= 100.0:
                raw_payout = raw_payout / 100.0
            scalars["payoutRatio"] = raw_payout

    return scalars, None

def main():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found.")
        sys.exit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    tickers = config.get("tickers", [])
    if not tickers:
        sys.exit(0)

    os.makedirs(DATA_DIR, exist_ok=True)
    damodaran = fetch_damodaran_benchmarks()

    all_data = {}
    errors = {}
    success_count = 0

    print(f"Fetching data for {len(tickers)} tickers...")
    for sym in tickers:
        sym_clean = sym.strip().upper()
        scalars, err = fetch_ticker_data(sym_clean)
        if scalars:
            raw_ind = scalars.get("industry", "")
            mapped_ind = DAMODARAN_MAP.get(raw_ind, raw_ind)
            scalars["_industryBenchmark"] = damodaran.get(mapped_ind, {})
            scalars["_industryName"] = mapped_ind

            all_data[sym_clean] = scalars
            success_count += 1
            print(f"-> SUCCESS: {sym_clean} [Price: {scalars.get('currentPrice')} | DivYield: {scalars.get('dividendYield')}]")
        else:
            errors[sym_clean] = err
            print(f"-> FAIL: {sym_clean} - {err}")
        time.sleep(0.8)

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
